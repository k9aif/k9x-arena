# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
"""SQLite store for K9X Arena (matches, answers, grades, stars, router
audit, uploads, review queue). One connection per call; thread-safe enough
for one running match plus the web UI."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional

from arena.settings import db_path

_LOCK = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  suite TEXT NOT NULL,
  suite_name TEXT,
  contenders TEXT NOT NULL,          -- JSON list of model tags
  judge TEXT NOT NULL,
  runs_per_task INTEGER NOT NULL,
  router_mode INTEGER NOT NULL,
  status TEXT NOT NULL,              -- queued | running | paused | completed | failed
  phase TEXT,
  error TEXT,
  created_at REAL NOT NULL,
  started_at REAL,
  finished_at REAL,
  settings TEXT                      -- JSON snapshot
);
CREATE TABLE IF NOT EXISTS tasks (
  match_id INTEGER NOT NULL,
  task_id TEXT NOT NULL,
  type TEXT NOT NULL,
  title TEXT,
  body TEXT NOT NULL,                -- JSON of the whole task
  screen TEXT,                       -- JSON: shield + guardian verdicts
  PRIMARY KEY (match_id, task_id)
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  match_id INTEGER NOT NULL,
  mode TEXT NOT NULL,                -- forced | router
  model TEXT NOT NULL,
  alias TEXT,
  task_id TEXT NOT NULL,
  run_no INTEGER NOT NULL,
  output TEXT,
  latency_ms INTEGER,
  refused INTEGER DEFAULT 0,
  error TEXT,
  created_at REAL NOT NULL,
  UNIQUE (match_id, mode, model, task_id, run_no)
);
CREATE TABLE IF NOT EXISTS grades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  grader TEXT NOT NULL,              -- code | extraction | reasoning | safety | judge_1 | judge_2 | review
  score REAL NOT NULL,
  grade TEXT,
  detail TEXT,
  created_at REAL NOT NULL,
  UNIQUE (run_id, grader)
);
CREATE TABLE IF NOT EXISTS stars (
  match_id INTEGER NOT NULL,
  model TEXT NOT NULL,
  task_type TEXT NOT NULL,
  quality REAL, consistency REAL, latency REAL, refusal_accuracy REAL,
  score REAL NOT NULL,
  stars INTEGER NOT NULL,
  p50_ms INTEGER, p95_ms INTEGER,
  PRIMARY KEY (match_id, model, task_type)
);
CREATE TABLE IF NOT EXISTS router_audit (
  match_id INTEGER NOT NULL,
  task_type TEXT NOT NULL,
  router_alias TEXT,
  router_model TEXT,
  best_model TEXT,
  regret REAL,
  verdict TEXT,                      -- match | mismatch | not_in_match
  PRIMARY KEY (match_id, task_type)
);
CREATE TABLE IF NOT EXISTS reports (
  match_id INTEGER PRIMARY KEY,
  body TEXT NOT NULL                 -- JSON: leaderboard, recommended yaml, ...
);
CREATE TABLE IF NOT EXISTS uploads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_name TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  kind TEXT NOT NULL,                -- suite | document
  precheck TEXT,
  guardian TEXT,                     -- safe | blocked | unavailable
  reason TEXT,
  accepted INTEGER NOT NULL,
  suite_key TEXT,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  match_id INTEGER NOT NULL,
  run_id INTEGER NOT NULL UNIQUE,
  reason TEXT NOT NULL,
  status TEXT NOT NULL,              -- pending | decided
  decided_score REAL,
  decided_by TEXT,
  created_at REAL NOT NULL,
  decided_at REAL
);
CREATE TABLE IF NOT EXISTS screen_cache (
  key TEXT PRIMARY KEY,              -- sha256(guardian model + prompt)
  screen TEXT NOT NULL,              -- JSON verdicts
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  match_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  text TEXT NOT NULL,
  created_at REAL NOT NULL
);
"""


@contextmanager
def conn():
    with _LOCK:
        c = sqlite3.connect(db_path(), timeout=30)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()


def init() -> None:
    with conn() as c:
        c.executescript(SCHEMA)


def _rows(cur) -> List[Dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]


# ── matches ─────────────────────────────────────────────────────────────────
def create_match(suite: str, suite_name: str, contenders: List[str], judge: str,
                 runs_per_task: int, router_mode: bool, settings: Dict[str, Any]) -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO matches (suite, suite_name, contenders, judge, runs_per_task, router_mode,"
            " status, phase, created_at, settings) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (suite, suite_name, json.dumps(contenders), judge, runs_per_task, int(router_mode),
             "queued", "queued", time.time(), json.dumps(settings)),
        )
        return int(cur.lastrowid)


def get_match(match_id: int) -> Optional[Dict[str, Any]]:
    with conn() as c:
        r = c.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
    if not r:
        return None
    m = dict(r)
    m["contenders"] = json.loads(m["contenders"])
    m["settings"] = json.loads(m["settings"] or "{}")
    m["router_mode"] = bool(m["router_mode"])
    return m


def list_matches(limit: int = 50) -> List[Dict[str, Any]]:
    with conn() as c:
        rows = _rows(c.execute("SELECT * FROM matches ORDER BY id DESC LIMIT ?", (limit,)))
    for m in rows:
        m["contenders"] = json.loads(m["contenders"])
        m["settings"] = json.loads(m["settings"] or "{}")
        m["router_mode"] = bool(m["router_mode"])
    return rows


def update_match(match_id: int, **fields) -> None:
    if not fields:
        return
    keys = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE matches SET {keys} WHERE id=?", (*fields.values(), match_id))


def delete_match(match_id: int) -> None:
    with conn() as c:
        run_ids = [r["id"] for r in c.execute("SELECT id FROM runs WHERE match_id=?", (match_id,))]
        if run_ids:
            q = ",".join("?" * len(run_ids))
            c.execute(f"DELETE FROM grades WHERE run_id IN ({q})", run_ids)
        for table in ("runs", "tasks", "stars", "router_audit", "reports", "reviews", "events"):
            c.execute(f"DELETE FROM {table} WHERE match_id=?", (match_id,))
        c.execute("DELETE FROM matches WHERE id=?", (match_id,))


# ── tasks ───────────────────────────────────────────────────────────────────
def save_tasks(match_id: int, tasks: Iterable[Dict[str, Any]]) -> None:
    with conn() as c:
        for t in tasks:
            c.execute(
                "INSERT OR IGNORE INTO tasks (match_id, task_id, type, title, body) VALUES (?,?,?,?,?)",
                (match_id, t["id"], t["type"], t.get("title", ""), json.dumps(t)),
            )


def set_task_screen(match_id: int, task_id: str, screen: Dict[str, Any]) -> None:
    with conn() as c:
        c.execute("UPDATE tasks SET screen=? WHERE match_id=? AND task_id=?",
                  (json.dumps(screen), match_id, task_id))


def get_tasks(match_id: int) -> List[Dict[str, Any]]:
    with conn() as c:
        rows = _rows(c.execute("SELECT * FROM tasks WHERE match_id=? ORDER BY rowid", (match_id,)))
    out = []
    for r in rows:
        t = json.loads(r["body"])
        t["screen"] = json.loads(r["screen"]) if r["screen"] else None
        out.append(t)
    return out


# ── runs & grades ───────────────────────────────────────────────────────────
def run_exists(match_id: int, mode: str, model: str, task_id: str, run_no: int) -> bool:
    with conn() as c:
        return c.execute(
            "SELECT 1 FROM runs WHERE match_id=? AND mode=? AND model=? AND task_id=? AND run_no=?",
            (match_id, mode, model, task_id, run_no)).fetchone() is not None


def save_run(match_id: int, mode: str, model: str, alias: str, task_id: str, run_no: int,
             output: str, latency_ms: int, refused: bool, error: Optional[str]) -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT OR REPLACE INTO runs (match_id, mode, model, alias, task_id, run_no, output,"
            " latency_ms, refused, error, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (match_id, mode, model, alias, task_id, run_no, output, latency_ms, int(refused), error, time.time()),
        )
        return int(cur.lastrowid)


def get_runs(match_id: int, mode: Optional[str] = None) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM runs WHERE match_id=?"
    args: List[Any] = [match_id]
    if mode:
        sql += " AND mode=?"
        args.append(mode)
    with conn() as c:
        return _rows(c.execute(sql + " ORDER BY id", args))


def save_grade(run_id: int, grader: str, score: float, grade: str = "", detail: Any = None) -> None:
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO grades (run_id, grader, score, grade, detail, created_at) VALUES (?,?,?,?,?,?)",
            (run_id, grader, float(score), grade, json.dumps(detail) if detail is not None else None, time.time()),
        )


def get_grades(match_id: int) -> List[Dict[str, Any]]:
    with conn() as c:
        rows = _rows(c.execute(
            "SELECT g.* FROM grades g JOIN runs r ON r.id=g.run_id WHERE r.match_id=? ORDER BY g.id", (match_id,)))
    for g in rows:
        g["detail"] = json.loads(g["detail"]) if g["detail"] else None
    return rows


# ── results ─────────────────────────────────────────────────────────────────
def save_stars(match_id: int, rows: List[Dict[str, Any]]) -> None:
    with conn() as c:
        c.execute("DELETE FROM stars WHERE match_id=?", (match_id,))
        for r in rows:
            c.execute(
                "INSERT INTO stars VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (match_id, r["model"], r["task_type"], r["quality"], r["consistency"], r["latency"],
                 r["refusal_accuracy"], r["score"], r["stars"], r["p50_ms"], r["p95_ms"]),
            )


def get_stars(match_id: int) -> List[Dict[str, Any]]:
    with conn() as c:
        return _rows(c.execute("SELECT * FROM stars WHERE match_id=?", (match_id,)))


def save_audit(match_id: int, rows: List[Dict[str, Any]]) -> None:
    with conn() as c:
        c.execute("DELETE FROM router_audit WHERE match_id=?", (match_id,))
        for r in rows:
            c.execute(
                "INSERT INTO router_audit VALUES (?,?,?,?,?,?,?)",
                (match_id, r["task_type"], r.get("router_alias"), r.get("router_model"),
                 r.get("best_model"), r.get("regret"), r["verdict"]),
            )


def get_audit(match_id: int) -> List[Dict[str, Any]]:
    with conn() as c:
        return _rows(c.execute("SELECT * FROM router_audit WHERE match_id=?", (match_id,)))


def save_report(match_id: int, body: Dict[str, Any]) -> None:
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO reports VALUES (?,?)", (match_id, json.dumps(body)))


def get_report(match_id: int) -> Optional[Dict[str, Any]]:
    with conn() as c:
        r = c.execute("SELECT body FROM reports WHERE match_id=?", (match_id,)).fetchone()
    return json.loads(r["body"]) if r else None


# ── uploads ─────────────────────────────────────────────────────────────────
def save_upload(**row) -> int:
    row.setdefault("created_at", time.time())
    keys = ", ".join(row)
    with conn() as c:
        cur = c.execute(f"INSERT INTO uploads ({keys}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))
        return int(cur.lastrowid)


def list_uploads(limit: int = 20) -> List[Dict[str, Any]]:
    with conn() as c:
        return _rows(c.execute("SELECT * FROM uploads ORDER BY id DESC LIMIT ?", (limit,)))


# ── reviews ─────────────────────────────────────────────────────────────────
def add_review(match_id: int, run_id: int, reason: str) -> None:
    with conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO reviews (match_id, run_id, reason, status, created_at) VALUES (?,?,?,?,?)",
            (match_id, run_id, reason, "pending", time.time()),
        )


def list_reviews(match_id: Optional[int] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM reviews WHERE 1=1"
    args: List[Any] = []
    if match_id is not None:
        sql += " AND match_id=?"
        args.append(match_id)
    if status:
        sql += " AND status=?"
        args.append(status)
    with conn() as c:
        return _rows(c.execute(sql + " ORDER BY id", args))


def decide_review(review_id: int, score: float, by: str) -> Optional[Dict[str, Any]]:
    with conn() as c:
        r = c.execute("SELECT * FROM reviews WHERE id=?", (review_id,)).fetchone()
        if not r:
            return None
        c.execute("UPDATE reviews SET status='decided', decided_score=?, decided_by=?, decided_at=? WHERE id=?",
                  (float(score), by, time.time(), review_id))
        return dict(r)


# ── screening cache (a prompt is screened once per Guardian model) ─────────
def get_cached_screen(key: str) -> Optional[Dict[str, Any]]:
    with conn() as c:
        r = c.execute("SELECT screen FROM screen_cache WHERE key=?", (key,)).fetchone()
    return json.loads(r["screen"]) if r else None


def put_cached_screen(key: str, screen: Dict[str, Any]) -> None:
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO screen_cache VALUES (?,?,?)", (key, json.dumps(screen), time.time()))


# ── events ──────────────────────────────────────────────────────────────────
def add_event(match_id: int, kind: str, text: str) -> Dict[str, Any]:
    now = time.time()
    with conn() as c:
        cur = c.execute("INSERT INTO events (match_id, kind, text, created_at) VALUES (?,?,?,?)",
                        (match_id, kind, text, now))
        return {"id": int(cur.lastrowid), "match_id": match_id, "kind": kind, "text": text, "created_at": now}


def get_events(match_id: int, after_id: int = 0, limit: int = 200) -> List[Dict[str, Any]]:
    with conn() as c:
        return _rows(c.execute(
            "SELECT * FROM events WHERE match_id=? AND id>? ORDER BY id LIMIT ?", (match_id, after_id, limit)))
