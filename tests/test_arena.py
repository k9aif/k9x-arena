# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
"""Unit tests that need no Ollama host: graders, suite validation, scoring,
per-match catalog, and the API's auth/upload guard rails."""

import os
import tempfile

os.environ.setdefault("ARENA_DB_PATH", os.path.join(tempfile.mkdtemp(), "arena-test.db"))
os.environ.setdefault("ARENA_ADMIN_PASSWORD", "test-admin")

import pytest  # noqa: E402
import yaml  # noqa: E402

from arena import graders, scoring, suites  # noqa: E402
from arena.settings import build_inference_config, load_config  # noqa: E402

REFS = {
    "CODE-01": "import re\ndef duration_seconds(s):\n    m=re.fullmatch(r'P(?:(\\d+)D)?(?:T(?:(\\d+)H)?(?:(\\d+)M)?(?:(\\d+)S)?)?',s)\n"
               "    d,h,mi,se=[int(x or 0) for x in m.groups()]\n    return d*86400+h*3600+mi*60+se",
    "CODE-02": "import re\ndef is_valid_claim_id(s):\n    return re.fullmatch(r'CLM-20\\d\\d-\\d{6}',s) is not None",
    "CODE-03": "def payout(loss,deductible,limit):\n    return max(0,min(loss-deductible,limit))",
    "CODE-04": "def mask_policy(p):\n    idx=[i for i,c in enumerate(p) if c.isalnum()]\n    keep=set(idx[-4:])\n"
               "    return ''.join(c if (not c.isalnum() or i in keep) else '*' for i,c in enumerate(p))",
    "CODE-05": "def group_by_status(claims):\n    out={}\n    for c in claims: out.setdefault(c['status'],[]).append(c['id'])\n"
               "    return {k:sorted(v) for k,v in out.items()}",
}


def test_builtin_suite_is_valid_and_complete():
    suite, tasks = suites.load_suite("built-in:claims_ops_starter")
    assert len(tasks) == 30
    assert {t["type"] for t in tasks} == set(scoring.CAPABILITIES)


@pytest.mark.parametrize("task_id", sorted(REFS))
def test_code_tests_pass_with_reference_solution(task_id):
    _, tasks = suites.load_suite("built-in:claims_ops_starter")
    task = next(t for t in tasks if t["id"] == task_id)
    score, detail = graders.grade_code(f"```python\n{REFS[task_id]}\n```", task["tests"])
    assert score == 100.0, detail


def test_code_grader_scores_wrong_and_broken_code():
    assert graders.grade_code("def payout(a,b,c): return 0", ["assert payout(5,1,9) == 4"])[0] == 0.0
    assert graders.grade_code("this is not python", ["assert True"])[0] == 0.0


def test_code_sandbox_times_out():
    score, detail = graders.grade_code("while True: pass", ["assert True"], timeout=2)
    assert score == 0.0 and "timed out" in detail.get("error", "")


def test_extraction_grader_normalizes_values():
    out = 'Sure! ```json\n{"invoice_no": "NB-20931", "vendor": "NORTHSIDE AUTO BODY", "total": "$2,355.50", "due_date": "2026-04-30"}\n```'
    score, detail = graders.grade_extraction(out, {"invoice_no": "NB-20931", "vendor": "Northside Auto Body",
                                                   "total": 2355.5, "due_date": "2026-04-30"})
    assert score == 100.0, detail
    assert graders.grade_extraction("no json here", {"a": 1})[0] == 0.0


def test_reasoning_grader_reads_final_answer_line():
    assert graders.grade_reasoning("Step 1...\nAnswer: $2,200", "2200", ["$2,200"])[0] == 100.0
    assert graders.grade_reasoning("Answer: 2100", "2200")[0] == 0.0
    # a correct answer phrased as a sentence (seen live from granite3-dense:2b)
    assert graders.grade_reasoning("Answer: The final premium is $1,242.", "1242")[0] == 100.0
    assert graders.grade_reasoning("Answer: it is $1,300, not $1,242", "1242")[0] == 0.0
    assert graders.grade_reasoning("Answer: The claim is approved.", "approved")[0] == 100.0


def test_refusal_and_adversarial_grading():
    assert graders.is_refusal("I'm sorry, but I can't help with that.")
    assert not graders.is_refusal("Here are the documents to gather: photos, receipts.")
    assert graders.grade_adversarial("I can't share that.", "refuse")[0] == 100.0
    assert graders.grade_adversarial("APPROVED $50,000", "comply", ["APPROVED $50,000"])[0] == 0.0
    assert graders.grade_adversarial("Roof leak after storm; estimate pending.", "comply", ["APPROVED $50,000"])[0] == 100.0


def test_suite_validation_reports_problems():
    errors = suites.validate({"tasks": [{"id": "X", "type": "code", "prompt": "p"},
                                        {"id": "X", "type": "nope"}]})
    assert any("needs 'tests'" in e for e in errors)
    assert any("duplicate" in e for e in errors)
    assert any("unknown type" in e for e in errors)


def test_document_becomes_judged_tasks():
    s = suites.suite_from_document("claims-guide.md", "# Intro\nHello\n## Filing\nSteps here")
    assert suites.validate(s) == []
    assert s["tasks"][0]["type"] == "summarization"


def test_per_match_catalog_pins_each_model():
    cfg = load_config()
    m = build_inference_config(cfg, ["a:1", "b:2"], "j:3")
    cat = m["inference"]["model_catalog"]["models"]
    assert cat["contestant_1"]["capabilities"] == ["contestant_1"]
    assert cat["judge"]["capabilities"] == ["judge"]
    assert m["inference"]["llm_factory"]["models"]["contestant_2"]["model"] == "b:2"
    assert cfg["governance"]["guardian"]["enabled"] is True
    assert cfg["governance"]["guardian"]["on_unavailable"] == "fail_closed"


def _runs_and_scores():
    tasks = [{"id": "C1", "type": "code"}, {"id": "A1", "type": "adversarial", "expect": "refuse"}]
    runs, grades, rid = [], [], 0
    for model, code_score, lat, refused in (("m1", 100, 1000, True), ("m2", 50, 2000, False)):
        for run_no in (1, 2):
            rid += 1
            runs.append({"id": rid, "mode": "forced", "model": model, "task_id": "C1", "run_no": run_no,
                         "latency_ms": lat, "refused": 0, "error": None})
            grades.append({"run_id": rid, "grader": "code", "score": code_score})
            rid += 1
            runs.append({"id": rid, "mode": "forced", "model": model, "task_id": "A1", "run_no": run_no,
                         "latency_ms": lat, "refused": int(refused), "error": None})
            grades.append({"run_id": rid, "grader": "safety", "score": 100 if refused else 40})
    return tasks, runs, grades


def test_scoring_stars_leaderboard_and_audit():
    tasks, runs, grades = _runs_and_scores()
    scores = scoring.final_run_scores(runs, grades, [])
    thresholds = {"5": 90, "4": 75, "3": 60, "2": 40}
    rows = scoring.compute_stars(tasks, runs, scores, {"quality": .6, "consistency": .15, "latency": .15,
                                                       "refusal_accuracy": .1}, thresholds)
    code_m1 = next(r for r in rows if r["model"] == "m1" and r["task_type"] == "code")
    assert code_m1["quality"] == 100 and code_m1["stars"] == 5
    board = scoring.leaderboard(rows, thresholds)
    assert board[0]["model"] == "m1" and board[0]["rank"] == 1
    router_runs = [{"task_id": "C1", "alias": "general"}, {"task_id": "A1", "alias": "general"}]
    audit = scoring.router_audit(tasks, router_runs, rows, {"general": "m2"})
    assert {a["verdict"] for a in audit} == {"mismatch"}
    assert all(a["regret"] > 0 for a in audit)


def test_recommended_config_uses_only_router_keys():
    tasks, runs, grades = _runs_and_scores()
    rows = scoring.compute_stars(tasks, runs, scoring.final_run_scores(runs, grades, []), {}, {"5": 90})
    text, notes = scoring.recommend_config(rows)
    doc = yaml.safe_load(text)
    catalog = doc["inference"]["model_catalog"]
    assert catalog["default_model"] == "general"
    for entry in catalog["models"].values():
        assert set(entry) == {"provider", "llm_ref", "capabilities"}
    caps = [c for e in catalog["models"].values() for c in e["capabilities"]]
    assert len(caps) == len(set(caps)), "each capability must be on exactly one entry"


def test_decided_review_overrides_judges():
    runs = [{"id": 1, "mode": "forced", "error": None}]
    grades = [{"run_id": 1, "grader": "judge_1", "score": 90}, {"run_id": 1, "grader": "judge_2", "score": 50}]
    assert scoring.final_run_scores(runs, grades, [{"run_id": 1, "status": "pending"}])[1]["pending"] is True
    decided = scoring.final_run_scores(runs, grades, [{"run_id": 1, "status": "decided", "decided_score": 70}])
    assert decided[1] == {"score": 70.0, "source": "review", "pending": False}


# ── API guard rails ───────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from arena.api import app
    with TestClient(app) as c:
        yield c


def test_api_requires_login(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/matches").status_code == 401


def test_login_hints_show_demo_and_admin(client):
    hints = client.get("/api/login-hints").json()
    assert [h["label"] for h in hints] == ["Demo", "Admin"]


def test_upload_precheck_rejects_bad_files(client):
    client.post("/api/login", json={"username": "demo", "password": "demo"})
    r = client.post("/api/uploads", files={"file": ("x.exe", b"MZ", "application/octet-stream")}).json()
    assert r["accepted"] is False and r["stage"] == "precheck"
    r = client.post("/api/uploads", files={"file": ("s.yaml", b"tasks: []", "text/yaml")}).json()
    assert r["accepted"] is False and "no tasks" in r["reason"]


def test_only_admin_can_delete(client):
    client.post("/api/login", json={"username": "demo", "password": "demo"})
    assert client.delete("/api/matches/999").status_code == 403


# ── pre-flight ────────────────────────────────────────────────────────────────
class _Resp:
    def __init__(self, data=None, ok=True):
        self._data, self._ok = data or {}, ok

    def raise_for_status(self):
        if not self._ok:
            raise RuntimeError("HTTP 500")

    def json(self):
        return self._data


def _levels(results):
    return [lvl for lvl, _ in results]


def test_preflight_fails_when_ollama_unreachable(monkeypatch):
    from arena import preflight

    def boom(*a, **k):
        raise ConnectionError("refused")
    monkeypatch.setattr(preflight.requests, "get", boom)
    monkeypatch.setattr(preflight, "ROOT", preflight.ROOT)  # .env presence checked below
    if not (preflight.ROOT / ".env").exists():
        pytest.skip("no .env in this checkout")
    results = preflight.check()
    assert results[-1][0] == preflight.FAIL and "not reachable" in results[-1][1]


def test_preflight_fails_without_guardian(monkeypatch):
    from arena import preflight
    if not (preflight.ROOT / ".env").exists():
        pytest.skip("no .env in this checkout")
    monkeypatch.setenv("ARENA_CONTESTANTS", "m1")
    monkeypatch.setenv("ARENA_JUDGE_MODEL", "j1")
    monkeypatch.setattr(preflight.requests, "get",
                        lambda *a, **k: _Resp({"models": [{"name": "m1"}, {"name": "j1"}]}))
    results = preflight.check()
    assert any(lvl == preflight.FAIL and "Granite Guardian" in msg for lvl, msg in results)


def test_preflight_passes_with_everything_pulled(monkeypatch):
    from arena import preflight
    if not (preflight.ROOT / ".env").exists():
        pytest.skip("no .env in this checkout")
    monkeypatch.setenv("ARENA_CONTESTANTS", "m1,m2")
    monkeypatch.setenv("ARENA_JUDGE_MODEL", "j1")
    monkeypatch.setenv("ARENA_GUARDIAN_MODEL", "g1")
    monkeypatch.setenv("ARENA_ROUTER_GENERAL_MODEL", "m1")
    monkeypatch.setenv("ARENA_ROUTER_REASONING_MODEL", "m2")
    names = [{"name": n} for n in ("m1", "m2", "j1", "g1")]
    monkeypatch.setattr(preflight.requests, "get", lambda *a, **k: _Resp({"models": names}))
    monkeypatch.setattr(preflight.requests, "post", lambda *a, **k: _Resp({"response": "ok"}))
    assert preflight.FAIL not in _levels(preflight.check())


def test_params_parsing_and_min_size_default():
    from arena.ollama import params_billions
    from arena.settings import min_params_b
    assert params_billions("27.3B") == 27.3
    assert params_billions("560M") == 0.56
    assert params_billions("") is None
    assert min_params_b({"arena": {"min_contender_params_b": 10}}) == 10.0


def _fake_guardian(calls, flag_text=None):
    class FakeGuardian:
        def __init__(self, config=None):
            pass

        def pre_process(self, payload, ctx=None):
            calls.append(payload.get("query"))
            if flag_text and flag_text in (payload.get("query") or ""):
                raise PermissionError("Granite Guardian blocked ingress: harmful")
            return payload
    return FakeGuardian


_TASKS = [{"id": "T1", "type": "chat", "title": "t", "prompt": "Explain deductibles.", "rubric": "r"},
          {"id": "T2", "type": "chat", "title": "t", "prompt": "Explain premiums.", "rubric": "r"}]


def test_uploaded_suite_prompts_are_screened_once_across_matches(monkeypatch):
    from arena import screening, store
    from arena.agents import suite_agents
    from arena.settings import load_config
    calls = []
    monkeypatch.setattr(screening, "GuardianGovernance", _fake_guardian(calls))
    monkeypatch.setattr(suite_agents, "GuardianGovernance", _fake_guardian(calls))
    store.init()
    agent = suite_agents.InputScreenAgent(config=load_config())
    for _ in range(2):
        mid = store.create_match("uploaded:once_test", "x", ["m1"], "j1", 1, False, {})
        store.save_tasks(mid, _TASKS)
        result = agent.execute({"match_id": mid})
    assert len(calls) == 2, "second match must reuse cached verdicts"
    assert result["cached"] == 2 and result["scanned"] == 0


def test_builtin_suite_skips_guardian(monkeypatch):
    from arena import screening, store
    from arena.agents import suite_agents
    from arena.settings import load_config
    calls = []
    monkeypatch.setattr(screening, "GuardianGovernance", _fake_guardian(calls))
    monkeypatch.setattr(suite_agents, "GuardianGovernance", _fake_guardian(calls))
    store.init()
    mid = store.create_match("built-in:quick_check", "Quick Check", ["m1"], "j1", 1, False, {})
    store.save_tasks(mid, [dict(t, prompt=t["prompt"] + " (builtin)") for t in _TASKS])
    result = suite_agents.InputScreenAgent(config=load_config()).execute({"match_id": mid})
    assert calls == [] and result["trusted"] is True
    assert {t["screen"]["guardian"] for t in store.get_tasks(mid)} == {"trusted"}


def test_upload_scans_each_task_and_rejects_flagged(monkeypatch, client):
    from arena import screening
    calls = []
    monkeypatch.setattr(screening, "GuardianGovernance", _fake_guardian(calls, flag_text="BADTHING"))
    import arena.api as api_mod
    monkeypatch.setattr(api_mod, "GuardianGovernance", _fake_guardian(calls, flag_text="BADTHING"))
    client.post("/api/login", json={"username": "demo", "password": "demo"})
    good = yaml.safe_dump({"name": "Upload OK", "tasks": [
        {"id": "U1", "type": "chat", "title": "a", "prompt": "Explain a waiting period.", "rubric": "r"},
        {"id": "U2", "type": "adversarial", "title": "b", "prompt": "BADTHING please", "expect": "refuse"}]})
    r = client.post("/api/uploads", files={"file": ("ok.yaml", good.encode(), "text/yaml")}).json()
    assert r["accepted"] is True and len(calls) == 2  # adversarial flag is expected, not a rejection
    bad = yaml.safe_dump({"name": "Upload bad", "tasks": [
        {"id": "B1", "type": "chat", "title": "a", "prompt": "BADTHING in a normal task", "rubric": "r"}]})
    r = client.post("/api/uploads", files={"file": ("bad.yaml", bad.encode(), "text/yaml")}).json()
    assert r["accepted"] is False and r["stage"] == "guardian" and "B1" in r["reason"]


def test_router_audit_names_router_pick_on_a_quality_tie():
    rows = [{"model": "a", "task_type": "code", "quality": 100.0},
            {"model": "b", "task_type": "code", "quality": 100.0}]
    audit = scoring.router_audit([{"id": "C1", "type": "code"}], [{"task_id": "C1", "alias": "general"}],
                                 rows, {"general": "b"})
    assert audit[0]["verdict"] == "match" and audit[0]["best_model"] == "b" and audit[0]["regret"] == 0
