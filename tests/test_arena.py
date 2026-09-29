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
    # pinned contest runs: the learned layer / circuit breaker must be off
    assert m["inference"]["router"]["learning"]["enabled"] is False


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


def test_scoring_stars_and_leaderboard():
    tasks, runs, grades = _runs_and_scores()
    scores = scoring.final_run_scores(runs, grades, [])
    thresholds = {"5": 90, "4": 75, "3": 60, "2": 40}
    rows = scoring.compute_stars(tasks, runs, scores, {"quality": .6, "consistency": .15, "latency": .15,
                                                       "refusal_accuracy": .1}, thresholds)
    code_m1 = next(r for r in rows if r["model"] == "m1" and r["task_type"] == "code")
    assert code_m1["quality"] == 100 and code_m1["stars"] == 5
    board = scoring.leaderboard(rows, thresholds)
    assert board[0]["model"] == "m1" and board[0]["rank"] == 1


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


def test_quality_tie_broken_by_overall_score():
    rows = [{"model": "slow", "task_type": "code", "quality": 100.0, "score": 88.0},
            {"model": "fast", "task_type": "code", "quality": 100.0, "score": 99.0}]
    best = scoring.best_by_type(rows)
    assert best["code"]["model"] == "fast" and best["code"]["tie_broken"] is True
    _, notes = scoring.recommend_config([dict(r, spread=0, p50_ms=1, p95_ms=1, over_refusals=0, answers=1,
                                              consistency=100, latency=100, refusal_accuracy=100, stars=5,
                                              pending=0) for r in rows])
    assert "tied with slow" in notes[0] and "chosen on overall score 99.0" in notes[0]


def test_verdict_clear_speed_and_draw():
    b = lambda m, q, s: {"model": m, "quality": q, "score": s}
    assert scoring.verdict([b("a", 95, 90), b("b", 80, 85)])["kind"] == "clear"
    v = scoring.verdict([b("a", 100, 100), b("b", 100, 89), b("c", 91, 85)])
    assert v["kind"] == "speed" and v["tied"] == ["a", "b"]
    assert scoring.verdict([b("a", 100, 95), b("b", 99, 94)])["kind"] == "draw"


def test_judge_reliability_flags_lenient_judge():
    lenient = scoring.judge_reliability([{"score": 85}, {"score": 90}], [100, 95, 98])
    assert lenient["checked"] and lenient["reliable"] is False
    fair = scoring.judge_reliability([{"score": 20}, {"score": 30}], [90, 85])
    assert fair["reliable"] is True
    assert scoring.judge_reliability([], [90])["checked"] is False


def test_second_judge_entry_and_think_stripping():
    from arena.agents.grading_agents import judged_text
    from arena.settings import build_inference_config, load_config
    cfg = load_config()
    m = build_inference_config(cfg, ["a:1"], "j:1")
    assert m["inference"]["model_catalog"]["models"]["judge_b"]["capabilities"] == ["judge_b"]
    assert m["inference"]["llm_factory"]["models"]["judge_b"]["temperature"] > 0  # resampled when no judge 2
    cfg["arena"]["judge_model_2"] = "k:2"
    m2 = build_inference_config(cfg, ["a:1"], "j:1")
    assert m2["inference"]["llm_factory"]["models"]["judge_b"]["model"] == "k:2"
    assert judged_text("<think>long reasoning</think>Final answer.") == "Final answer."


def test_rounds_capped_at_three(client):
    client.post("/api/login", json={"username": "demo", "password": "demo"})
    r = client.post("/api/matches", json={"contenders": ["a:1"], "judge": "j:1",
                                          "suite": "built-in:quick_check", "runs_per_task": 5})
    assert r.status_code == 422


def test_tie_note_names_the_tied_models():
    rows = [{"model": "qwen3.8", "task_type": "code", "quality": 100.0, "score": 87.1},
            {"model": "gemma4", "task_type": "code", "quality": 100.0, "score": 85.3},
            {"model": "coder", "task_type": "code", "quality": 0.0, "score": 40.0}]
    full = [dict(r, spread=0, p50_ms=1, p95_ms=1, over_refusals=0, answers=1, consistency=100,
                 latency=100, refusal_accuracy=100, stars=4, pending=0) for r in rows]
    _, notes = scoring.recommend_config(full)
    assert notes == ["code: qwen3.8 (quality 100.0, tied with gemma4; chosen on overall score 87.1 vs gemma4 85.3)"]
    assert "coder" not in notes[0]


def test_router_test_stays_on_when_router_models_not_competing(client, monkeypatch):
    import arena.api as api_mod
    monkeypatch.setattr(api_mod.ollama, "list_models", lambda: [
        {"tag": t, "family": "f", "params_b": 8.0} for t in ("small-a:7b", "small-b:8b", "judge:8b")])
    monkeypatch.setattr(api_mod, "_guardian_live", lambda: {"live": True})
    monkeypatch.setattr(api_mod.engine, "start", lambda mid: None)
    monkeypatch.setitem(api_mod.CONFIG["arena"], "router_under_test",
                        {"general": {"model": "big-x:27b", "capabilities": ["general"]},
                         "reasoning": {"model": "big-y:31b", "capabilities": ["reasoning"]}})
    client.post("/api/login", json={"username": "demo", "password": "demo"})
    r = client.post("/api/matches", json={"contenders": ["small-a:7b", "small-b:8b"], "judge": "judge:8b",
                                          "suite": "built-in:quick_check", "runs_per_task": 1, "router_mode": True})
    assert r.status_code == 200, r.text
    m = client.get(f"/api/matches/{r.json()['id']}").json()["match"]
    assert m["router_mode"] is True and "your rules" in m["settings"]["router_note"]


# ── router test (held-out, K9ModelRouter learned routing) ─────────────────────
SQL = ["Write a SQL query returning the top {n} customers by claim total.",
       "Write a SQL query counting open claims per region for batch {n}.",
       "Write a SQL query listing adjusters with more than {n} pending claims.",
       "Write a SQL query finding duplicate claim numbers in table {n}."]
PROOF = ["Prove that the sum of the first {n} odd numbers is a perfect square.",
         "Prove by induction that {n} factorial exceeds two to the power n.",
         "Prove there are infinitely many primes, then discuss case {n}.",
         "Prove the triangle inequality for vectors in dimension {n}."]


def _clustered_match():
    """Same task type, two kinds of prompt: 'sql' wins on SQL, 'prover' on proofs."""
    tasks, quality = [], {}
    for i in range(3):
        for j, p in enumerate(SQL):
            tid = f"S{i}{j}"
            tasks.append({"id": tid, "type": "reasoning", "title": tid, "prompt": p.format(n=i + 3)})
            quality[tid] = {"sql": {"q": 92.0, "ms": 900.0}, "prover": {"q": 55.0, "ms": 2500.0}}
        for j, p in enumerate(PROOF):
            tid = f"P{i}{j}"
            tasks.append({"id": tid, "type": "reasoning", "title": tid, "prompt": p.format(n=i + 3)})
            quality[tid] = {"sql": {"q": 40.0, "ms": 900.0}, "prover": {"q": 90.0, "ms": 2500.0}}
    return tasks, quality


def test_router_test_learned_router_beats_best_single_model():
    from arena import router_eval
    tasks, quality = _clustered_match()
    rules = {"general": {"model": "sql", "capabilities": ["general", "chat"]},
             "reasoning": {"model": "prover", "capabilities": ["reasoning"]}}
    out = router_eval.evaluate(tasks, quality, ["sql", "prover"], rules)
    st = out["strategies"]
    assert out["available"] and out["tasks"] == 24
    assert st["learned"]["best_picks"] == 24                  # per prompt, not per task type
    assert st["learned"]["avg"] > st["single"]["avg"] > st["random"]["avg"]
    assert st["rules"]["avg"] == st["single"]["avg"]          # rules send all reasoning to 'prover'
    assert out["headroom_captured"] == 100 and out["vs_single"] > 0
    # SQL prompts share wording, so evidence overrules the rules there; the
    # proof prompts are worded too differently for the lexical embedder, so
    # the rules (prover) keep them -- also the right call.
    assert out["learned_decisions"] == 12
    assert all(r["strategy"] == "learned" for r in out["rows"] if r["task_id"].startswith("S"))
    sql_row = next(r for r in out["rows"] if r["task_id"] == "S00")
    assert sql_row["learned_model"] == "sql" and sql_row["strategy"] == "learned"


def test_router_test_leaves_the_task_out():
    """The held-out task's own score must not leak into its prediction."""
    from arena import router_eval
    tasks, quality = _clustered_match()
    quality["S00"] = {"sql": {"q": 10.0, "ms": 900.0}, "prover": {"q": 99.0, "ms": 2500.0}}
    row = next(r for r in router_eval.evaluate(tasks, quality, ["sql", "prover"], {})["rows"]
               if r["task_id"] == "S00")
    assert row["learned_model"] == "sql" and row["learned_q"] == 10.0   # an honest miss


def test_router_test_needs_enough_data():
    from arena import router_eval
    tasks, quality = _clustered_match()
    out = router_eval.evaluate(tasks[:2], quality, ["sql", "prover"], {})
    assert out["available"] is False


def test_task_quality_averages_forced_runs_only():
    from arena import router_eval
    tasks = [{"id": "T1", "type": "code"}, {"id": "T2", "type": "code", "screen": {"excluded": True}}]
    runs = [{"id": 1, "mode": "forced", "model": "a", "task_id": "T1", "latency_ms": 100},
            {"id": 2, "mode": "forced", "model": "a", "task_id": "T1", "latency_ms": 300},
            {"id": 3, "mode": "router", "model": "a", "task_id": "T1", "latency_ms": 1},
            {"id": 4, "mode": "forced", "model": "a", "task_id": "T2", "latency_ms": 1}]
    scores = {1: {"score": 80}, 2: {"score": 100}, 3: {"score": 0}, 4: {"score": 0}}
    assert router_eval.task_quality(tasks, runs, scores) == {"T1": {"a": {"q": 90.0, "ms": 200.0}}}


def test_router_test_one_task_per_type_falls_back_to_rules():
    """Quick Check shape: nothing similar to learn from, so rules decide (and say so)."""
    from arena import router_eval
    types = ["code", "extraction", "reasoning", "summarization", "chat", "adversarial"]
    tasks = [{"id": t, "type": t, "title": t, "prompt": f"A {t} task about claims."} for t in types]
    quality = {t: {"a": {"q": 90.0, "ms": None}, "b": {"q": 80.0, "ms": None}} for t in types}
    out = router_eval.evaluate(tasks, quality, ["a", "b"], {})
    assert out["without_evidence"] == 6 and out["learned_decisions"] == 0


def test_ui_script_defines_every_helper_it_calls():
    """Regression: an edit once deleted rememberMatches, startMatch and both
    event listeners from app.js; the Lobby froze on 'Loading contenders…'."""
    import re
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "web" / "app.js").read_text()
    defined = set(re.findall(r"(?:function\s+|const\s+|let\s+)([A-Za-z_]\w*)", js))
    for name in ("rememberMatches", "startMatch", "judgeNote", "routerNote", "statusBadge",
                 "renderLobby", "renderResults", "routerPanel", "routerKpis", "verdictTile"):
        assert name in defined, name
    assert "app.addEventListener('click'" in js and "app.addEventListener('change'" in js
    # every top-level helper called as name( must be defined somewhere
    js_code = re.sub(r"`[^`]*`|'[^'\n]*'|\"[^\"\n]*\"", "''", js)  # ignore strings/templates
    called = set(re.findall(r"(?<![.\w])([a-z]\w*)\(", js_code))
    builtins = {"if", "for", "while", "switch", "catch", "return", "typeof", "fetch", "setTimeout",
                "clearTimeout", "setInterval", "clearInterval", "alert", "confirm", "encodeURIComponent",
                "decodeURIComponent", "parseInt", "parseFloat", "isNaN", "requestAnimationFrame",
                "cancelAnimationFrame", "getComputedStyle", "function", "async", "await", "new",
                "record_feedback"}  # prose on the Architecture page, inside a nested template
    assert not (called - defined - builtins), called - defined - builtins


def test_rescore_refused_unless_completed_and_idle(client, monkeypatch):
    import arena.api as api_mod
    from arena import store
    client.post("/api/login", json={"username": "demo", "password": "demo"})
    mid = store.create_match("built-in:quick_check", "Quick Check", ["a:1", "b:2"], "j:3", 1, True, {})
    assert client.post(f"/api/matches/{mid}/rescore").status_code == 409        # not completed
    store.update_match(mid, status="completed")
    monkeypatch.setattr(api_mod.engine, "is_busy", lambda: True)
    assert client.post(f"/api/matches/{mid}/rescore").status_code == 409        # a match is running
    monkeypatch.setattr(api_mod.engine, "is_busy", lambda: False)
    called = []
    monkeypatch.setattr(api_mod.engine, "rescore", lambda m: called.append(m))
    assert client.post(f"/api/matches/{mid}/rescore").status_code == 200 and called == [mid]
    assert client.post("/api/matches/99999/rescore").status_code == 404


def test_rescore_gives_an_old_match_its_router_test():
    """End to end through ArenaRouter -> ArenaOrchestrator -> ReportSquad, no model call."""
    from arena import engine, store
    from tests.test_arena import _clustered_match
    tasks, quality = _clustered_match()
    mid = store.create_match("built-in:x", "X", ["sql", "prover"], "j:1", 1, True, {})
    store.save_tasks(mid, tasks)
    for t in tasks:
        for model, v in quality[t["id"]].items():
            store.save_run(mid, "forced", model, "a", t["id"], 1, "answer", int(v["ms"]), False, None)
    for run in store.get_runs(mid, "forced"):
        store.save_grade(run["id"], "reasoning", quality[run["task_id"]][run["model"]]["q"])
    store.update_match(mid, status="completed")
    engine.rescore(mid)
    rt = store.get_report(mid)["router_test"]
    assert rt["available"] and rt["strategies"]["learned"]["best_picks"] == 24
    assert store.get_match(mid)["status"] == "completed"


def test_recommendation_stays_on_the_leader_within_the_tie_margin():
    """Match #6: granite led summary by 0.5 and chat by 0.2 -- noise. Keep
    everything on the leader (qwen) instead of splitting the router."""
    q = {"code": (100, 100), "extraction": (100, 100), "reasoning": (100, 100),
         "summarization": (99.5, 100.0), "chat": (98.9, 99.1), "adversarial": (100, 100)}
    rows = []
    for t, (qq, gq) in q.items():
        rows.append({"model": "qwen", "task_type": t, "quality": qq, "score": 99.0})
        rows.append({"model": "granite", "task_type": t, "quality": gq, "score": 97.0})
    text, notes = scoring.recommend_config(rows, margin=2.0, leader="qwen")
    doc = yaml.safe_load(text)
    assert doc["inference"]["llm_factory"]["models"] == {"general": {"model": "qwen"}}
    assert "within the 2-point tie margin" in next(n for n in notes if n.startswith("summarization"))
    # a clear win still splits
    rows = [dict(r, quality=80.0) if r["model"] == "qwen" and r["task_type"] == "code" else r for r in rows]
    doc = yaml.safe_load(scoring.recommend_config(rows, margin=2.0, leader="qwen")[0])
    assert {"model": "granite"} in doc["inference"]["llm_factory"]["models"].values()
