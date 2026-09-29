# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
"""K9X Arena web API (FastAPI) and static UI. Thin: requests become store
reads or engine events; no model calls happen in a request handler except
the mandatory Granite Guardian scan of uploads."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import statistics
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import yaml
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from k9_aif_abb.k9_governance.guardian_governance import GuardianGovernance

from arena import __version__, engine, live, ollama, scoring, screening, store, suites
from arena.settings import (ROOT, TASK_TYPES, credentials, default_contestants, judge_model,
                            load_config, min_params_b, ollama_base_url, second_judge_model)

WEB = ROOT / "web"
CONFIG = load_config()


@asynccontextmanager
async def _lifespan(_app):
    store.init()
    engine.recover_after_restart()
    yield


app = FastAPI(title="K9X Arena", version=__version__, lifespan=_lifespan)
_sessions: Dict[str, Dict[str, str]] = {}
_guardian_cache: Dict[str, Any] = {"at": 0.0, "live": None, "detail": ""}


# ── auth ────────────────────────────────────────────────────────────────────
class Login(BaseModel):
    username: str
    password: str


def user(request: Request) -> Dict[str, str]:
    token = request.cookies.get("arena_session", "")
    u = _sessions.get(token)
    if not u:
        raise HTTPException(401, "sign in required")
    return u


def admin(u: Dict[str, str] = Depends(user)) -> Dict[str, str]:
    if u["role"] != "admin":
        raise HTTPException(403, "admin only")
    return u


@app.post("/api/login")
def login(body: Login, response: Response):
    entry = credentials().get(body.username)
    if not entry or not secrets.compare_digest(entry["password"], body.password):
        raise HTTPException(401, "wrong username or password")
    token = secrets.token_urlsafe(32)
    _sessions[token] = {"username": body.username, "role": entry["role"]}
    response.set_cookie("arena_session", token, httponly=True, samesite="lax")
    return {"username": body.username, "role": entry["role"]}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    _sessions.pop(request.cookies.get("arena_session", ""), None)
    response.delete_cookie("arena_session")
    return {"ok": True}


@app.get("/api/me")
def me(u: Dict[str, str] = Depends(user)):
    return u


@app.get("/api/login-hints")
def login_hints():
    """Shown on the sign-in page of this beta: the demo login and, when
    configured, the admin login, clearly labeled."""
    creds = credentials()
    order = {"demo": 0, "admin": 1}
    return [{"label": c["role"].title(), "username": n, "password": c["password"]}
            for n, c in sorted(creds.items(), key=lambda kv: order.get(kv[1]["role"], 9))]


# ── status ──────────────────────────────────────────────────────────────────
def _guardian_live() -> Dict[str, Any]:
    now = time.time()
    if _guardian_cache["live"] is not None and now - _guardian_cache["at"] < 120:
        return _guardian_cache
    model = CONFIG["governance"]["guardian"]["model"]
    try:
        r = requests.post(f"{ollama_base_url()}/api/generate",
                          json={"model": model, "prompt": "hi", "stream": False, "options": {"num_predict": 4}},
                          timeout=60)
        r.raise_for_status()
        _guardian_cache.update(at=now, live=True, detail=model)
    except Exception as exc:
        _guardian_cache.update(at=now, live=False, detail=f"{model}: {exc}"[:200])
    return _guardian_cache


@app.get("/api/health")
def health():
    return {"ok": True, "version": __version__}


@app.get("/api/status")
def status(u=Depends(user)):
    g = _guardian_live()
    return {
        "ollama": {"reachable": ollama.reachable(), "url": ollama_base_url()},
        "guardian": {"live": bool(g["live"]), "model": CONFIG["governance"]["guardian"]["model"],
                     "detail": g["detail"]},
        "gpu": ollama.loaded_models(),
        "running_match": live.running_match(),
        "version": __version__,
    }


# ── models & suites ─────────────────────────────────────────────────────────
@app.get("/api/models")
def models(all: bool = False, u=Depends(user)):
    try:
        pulled = ollama.list_models()
    except Exception as exc:
        raise HTTPException(502, f"Ollama not reachable at {ollama_base_url()}: {exc}")
    wins: Dict[str, int] = defaultdict(int)
    entered: Dict[str, int] = defaultdict(int)
    stars: Dict[str, List[int]] = defaultdict(list)
    best: Dict[str, Dict[str, float]] = defaultdict(dict)
    for m in store.list_matches(500):
        if m["status"] != "completed":
            continue
        for tag in m["contenders"]:
            entered[tag] += 1
        report = store.get_report(m["id"]) or {}
        if report.get("winner"):
            wins[report["winner"]["model"]] += 1
        for row in store.get_stars(m["id"]):
            stars[row["model"]].append(row["stars"])
            best[row["model"]][row["task_type"]] = max(best[row["model"]].get(row["task_type"], 0), row["quality"])
    guardian_model = CONFIG["governance"]["guardian"]["model"]
    for p in pulled:
        tag = p["tag"]
        p["matches"] = entered.get(tag, 0)
        p["wins"] = wins.get(tag, 0)
        p["avg_stars"] = round(statistics.mean(stars[tag]), 1) if stars.get(tag) else None
        p["best_at"] = max(best[tag], key=best[tag].get) if best.get(tag) else None
        p["is_guardian"] = tag == guardian_model
        p["is_embedding"] = "embed" in tag
    # Only top models by default: at least arena.min_contender_params_b billion
    # parameters. Models named in .env (contenders, judge) always stay listed.
    threshold = min_params_b(CONFIG)
    named = set(default_contestants(CONFIG)) | {judge_model(CONFIG)}
    usable = [p for p in pulled if not p["is_guardian"] and not p["is_embedding"]]
    small = [p for p in usable if p["tag"] not in named
             and p["params_b"] is not None and p["params_b"] < threshold]
    shown = usable if all else [p for p in usable if p not in small]
    return {"models": shown, "hidden_small": 0 if all else len(small), "min_params_b": threshold,
            "show_all": all, "default_contestants": default_contestants(CONFIG),
            "default_judge": judge_model(CONFIG), "guardian_model": guardian_model,
            "second_judge": str(CONFIG["arena"].get("judge_model_2", "")).strip(),
            "runs_per_task": CONFIG["arena"]["runs_per_task"], "router_mode": CONFIG["arena"]["router_mode"]}


@app.get("/api/suites")
def list_suites(u=Depends(user)):
    return suites.list_suites()


# ── uploads (Granite Guardian mandatory, fails closed) ─────────────────────
MAX_UPLOAD = 512 * 1024


@app.post("/api/uploads")
async def upload(file: UploadFile = File(...), u=Depends(user)):
    name = Path(file.filename or "upload").name
    raw = await file.read()
    sha = hashlib.sha256(raw).hexdigest()
    ext = Path(name).suffix.lower()

    def reject(stage: str, reason: str, guardian: str = ""):
        store.save_upload(file_name=name, sha256=sha, kind="unknown", precheck=stage, guardian=guardian,
                          reason=reason, accepted=0)
        return {"accepted": False, "stage": stage, "reason": reason, "file_name": name}

    if ext not in (".yaml", ".yml", ".md", ".txt"):
        return reject("precheck", "Upload a task suite (.yaml) or a source document (.md, .txt).")
    if len(raw) > MAX_UPLOAD:
        return reject("precheck", "File is larger than 512 KB.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return reject("precheck", "File is not UTF-8 text.")

    kind = "suite" if ext in (".yaml", ".yml") else "document"
    if kind == "suite":
        try:
            suite = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            return reject("precheck", f"Not valid YAML: {exc}"[:300])
        errors = suites.validate(suite)
        if errors:
            return reject("precheck", "; ".join(errors[:5]))
    else:
        suite = suites.suite_from_document(name, text)

    # Scan every task prompt now (cached), so a match never rescans this suite.
    def _screen_all():
        guardian = GuardianGovernance(config=CONFIG)
        flagged = []
        for t in suite["tasks"]:
            verdicts, _ = screening.screen_prompt(CONFIG, t["prompt"], guardian)
            if screening.excluded(t, verdicts):
                flagged.append(f"{t.get('id', '?')}: {verdicts.get('guardian_reason') or verdicts.get('shield_reason')}")
        return flagged

    try:
        flagged = await asyncio.to_thread(_screen_all)
    except screening.GuardianUnavailable as exc:
        return reject("guardian", "Granite Guardian is unavailable, so the upload was refused (fail-closed). "
                      + str(exc)[:200], guardian="unavailable")
    if flagged:
        return reject("guardian", "Granite Guardian flagged "
                      + ("; ".join(flagged[:3]) + (f" (+{len(flagged) - 3} more)" if len(flagged) > 3 else ""))[:400],
                      guardian="blocked")

    suite.setdefault("name", Path(name).stem)
    key = suites.save_uploaded_suite(Path(name).stem, suite)
    store.save_upload(file_name=name, sha256=sha, kind=kind, precheck="pass", guardian="safe",
                      reason="", accepted=1, suite_key=key)
    return {"accepted": True, "stage": "accepted", "file_name": name, "suite_key": key,
            "suite_name": suite.get("name"), "tasks": len(suite.get("tasks", []))}


# ── matches ─────────────────────────────────────────────────────────────────
class NewMatch(BaseModel):
    contenders: List[str] = Field(min_length=1, max_length=6)
    judge: str
    suite: str
    runs_per_task: int = Field(default=3, ge=1, le=5)
    router_mode: bool = True


@app.post("/api/matches")
def create_match(body: NewMatch, u=Depends(user)):
    if engine.is_busy():
        raise HTTPException(409, f"match {live.running_match()} is still running — one match at a time on one GPU")
    contenders = list(dict.fromkeys(c.strip() for c in body.contenders if c.strip()))
    if body.judge in contenders:
        raise HTTPException(400, "the judge cannot also be a contender")
    pulled_models = ollama.list_models()
    pulled = {m["tag"] for m in pulled_models}
    missing = [t for t in contenders + [body.judge] if t not in pulled]
    if missing:
        raise HTTPException(400, f"not pulled on the Ollama host: {', '.join(missing)}")
    if not _guardian_live()["live"]:
        raise HTTPException(503, "Granite Guardian is offline; the arena never runs unscreened")
    try:
        suite, tasks = suites.load_suite(body.suite)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    judge_2 = second_judge_model(CONFIG, contenders)
    if judge_2 and judge_2 not in pulled:
        judge_2 = ""
    families = {m["tag"]: m["family"] for m in pulled_models if m["tag"] in set(contenders) | {body.judge, judge_2}}
    match_id = store.create_match(body.suite, suite.get("name", body.suite), contenders, body.judge,
                                  body.runs_per_task, body.router_mode,
                                  {"tasks": len(tasks), "by": u["username"], "judge_2": judge_2,
                                   "families": families})
    engine.start(match_id)
    return {"id": match_id}


@app.get("/api/matches")
def list_matches(u=Depends(user)):
    out = []
    for m in store.list_matches(100):
        report = store.get_report(m["id"]) or {}
        winner = report.get("winner") or {}
        out.append({k: m[k] for k in ("id", "suite_name", "contenders", "judge", "status", "phase",
                                       "created_at", "started_at", "finished_at", "runs_per_task")}
                   | {"winner": winner.get("model"), "winner_score": winner.get("score")})
    return out


def _match_or_404(match_id: int) -> Dict[str, Any]:
    m = store.get_match(match_id)
    if not m:
        raise HTTPException(404, "match not found")
    return m


def _lanes(m: Dict[str, Any], tasks: List[Dict[str, Any]], runs: List[Dict[str, Any]],
           scores: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
    active = [t for t in tasks if not (t.get("screen") or {}).get("excluded")]
    per_model_total = len(active) * m["runs_per_task"]
    titles = {t["id"]: t for t in tasks}
    current = live.snapshot().get("current") if live.running_match() == m["id"] else None
    lanes = []
    for model in m["contenders"]:
        rs = [r for r in runs if r["mode"] == "forced" and r["model"] == model]
        graded = [scores[r["id"]]["score"] for r in rs if r["id"] in scores]
        state = ("done" if per_model_total and len(rs) >= per_model_total
                 else "answering" if current and current.get("model") == model
                 else "waiting" if rs else "queued")
        recent = []
        for r in rs[-4:][::-1]:
            t = titles.get(r["task_id"], {})
            s = scores.get(r["id"])
            recent.append({"task_id": r["task_id"], "type": t.get("type"), "title": t.get("title", ""),
                           "run_no": r["run_no"], "latency_ms": r["latency_ms"], "error": r["error"],
                           "refused": bool(r["refused"]), "preview": (r["output"] or "")[:160],
                           "score": s["score"] if s else None, "pending": bool(s and s["pending"])})
        lanes.append({"model": model, "state": state, "answers": len(rs), "total": per_model_total,
                      "score": round(statistics.mean(graded), 1) if graded else None, "recent": recent})
    return lanes


@app.get("/api/matches/{match_id}")
def get_match(match_id: int, u=Depends(user)):
    m = _match_or_404(match_id)
    tasks = store.get_tasks(match_id)
    runs = store.get_runs(match_id)
    reviews = store.list_reviews(match_id)
    scores = scoring.final_run_scores(runs, store.get_grades(match_id), reviews)
    snap = live.snapshot() if live.running_match() == match_id else {}
    per_task: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
    for r in runs:
        if r["mode"] == "forced" and r["id"] in scores:
            per_task[r["task_id"]][r["model"]].append(scores[r["id"]]["score"])
    task_scores = {tid: {mdl: round(statistics.mean(v), 1) for mdl, v in by_model.items()}
                   for tid, by_model in per_task.items()}
    return {
        "match": m,
        "task_scores": task_scores,
        "tasks": [{k: t.get(k) for k in ("id", "type", "title", "screen")} for t in tasks],
        "lanes": _lanes(m, tasks, runs, scores),
        "progress": snap.get("progress", {}),
        "current": snap.get("current"),
        "stars": store.get_stars(match_id),
        "audit": store.get_audit(match_id),
        "report": store.get_report(match_id),
        "reviews": reviews,
        "task_types": TASK_TYPES,
        "router_runs": len([r for r in runs if r["mode"] == "router"]),
    }


@app.get("/api/matches/{match_id}/events")
def events(match_id: int, after: int = 0, u=Depends(user)):
    return store.get_events(match_id, after)


@app.get("/api/matches/{match_id}/stream")
async def stream(match_id: int, request: Request, u=Depends(user)):
    _match_or_404(match_id)

    async def gen():
        last = 0
        while True:
            if await request.is_disconnected():
                break
            new = store.get_events(match_id, last)
            if new:
                last = new[-1]["id"]
            m = store.get_match(match_id)
            snap = live.snapshot() if live.running_match() == match_id else {}
            payload = {"status": m["status"], "phase": m["phase"], "error": m["error"],
                       "progress": snap.get("progress", {}), "current": snap.get("current"), "events": new}
            yield f"data: {json.dumps(payload)}\n\n"
            if m["status"] in ("completed", "failed", "paused") and not new:
                await asyncio.sleep(5)
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/matches/{match_id}/pause")
def pause(match_id: int, u=Depends(user)):
    if live.running_match() != match_id:
        raise HTTPException(409, "match is not running")
    live.request_pause()
    return {"ok": True}


@app.post("/api/matches/{match_id}/cancel")
def cancel(match_id: int, u=Depends(user)):
    if live.running_match() != match_id:
        raise HTTPException(409, "match is not running")
    live.request_cancel()
    return {"ok": True}


@app.post("/api/matches/{match_id}/resume")
def resume(match_id: int, u=Depends(user)):
    m = _match_or_404(match_id)
    if m["status"] not in ("paused", "failed"):
        raise HTTPException(409, f"match is {m['status']}")
    if not _guardian_live()["live"]:
        raise HTTPException(503, "Granite Guardian is offline; the arena never runs unscreened")
    try:
        engine.start(match_id)
    except engine.Busy as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True}


@app.delete("/api/matches/{match_id}")
def delete(match_id: int, u=Depends(admin)):
    if live.running_match() == match_id:
        raise HTTPException(409, "cancel the match before deleting it")
    store.delete_match(match_id)
    return {"ok": True}


@app.get("/api/matches/{match_id}/tasks/{task_id}")
def task_detail(match_id: int, task_id: str, u=Depends(user)):
    tasks = {t["id"]: t for t in store.get_tasks(match_id)}
    task = tasks.get(task_id)
    if not task:
        raise HTTPException(404, "task not found")
    runs = [r for r in store.get_runs(match_id) if r["task_id"] == task_id]
    grades = defaultdict(dict)
    for g in store.get_grades(match_id):
        grades[g["run_id"]][g["grader"]] = g
    reviews = {r["run_id"]: r for r in store.list_reviews(match_id)}
    safe_task = {k: v for k, v in task.items() if k not in ("tests", "expected", "answer", "aliases",
                                                            "must_not_contain")}
    return {
        "task": safe_task,
        "grading": {k: task.get(k) for k in ("tests", "expected", "answer", "reference", "rubric", "expect")
                    if task.get(k) is not None},
        "runs": [{**r, "grades": grades.get(r["id"], {}), "review": reviews.get(r["id"])} for r in runs],
    }


class Decision(BaseModel):
    score: float = Field(ge=0, le=100)


@app.post("/api/reviews/{review_id}")
def decide(review_id: int, body: Decision, u=Depends(admin)):
    r = store.decide_review(review_id, body.score, u["username"])
    if not r:
        raise HTTPException(404, "review not found")
    if not engine.is_busy():
        engine.rescore(r["match_id"])
    return {"ok": True}


@app.get("/api/reviews")
def reviews(status: Optional[str] = "pending", u=Depends(user)):
    out = []
    runs_by_match: Dict[int, Dict[int, Dict[str, Any]]] = {}
    for r in store.list_reviews(status=status):
        if r["match_id"] not in runs_by_match:
            runs_by_match[r["match_id"]] = {x["id"]: x for x in store.get_runs(r["match_id"])}
        run = runs_by_match[r["match_id"]].get(r["run_id"], {})
        out.append({**r, "task_id": run.get("task_id"), "model": run.get("model")})
    return out


@app.get("/api/matches/{match_id}/config.yaml")
def recommended(match_id: int, u=Depends(user)):
    report = store.get_report(match_id) or {}
    if not report.get("recommended_yaml"):
        raise HTTPException(404, "no recommendation yet")
    return PlainTextResponse(report["recommended_yaml"], headers={
        "Content-Disposition": f'attachment; filename="k9x_arena_match_{match_id}_model_catalog.yaml"'})


@app.get("/api/uploads")
def uploads(u=Depends(user)):
    return store.list_uploads()


# ── static UI ───────────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=str(WEB)), name="static")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")
