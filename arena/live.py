# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
"""Live match state shared by the engine, the agents and the web UI:
the running match id, pause/cancel flags, progress counters, and a bridge
from the framework's trace-events bus into the match's event log."""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional

from k9_aif_abb.k9_utils.trace_events import register_trace_callback

from arena import store
from arena.settings import TASK_TYPES

_lock = threading.RLock()
_state: Dict[str, Any] = {"match_id": None, "pause": False, "cancel": False, "progress": {}, "current": None}


class MatchStopped(Exception):
    """Raised inside agents when the user pauses or cancels the match."""


def begin(match_id: int) -> None:
    with _lock:
        _state.update({"match_id": match_id, "pause": False, "cancel": False, "progress": {}, "current": None})


def end() -> None:
    with _lock:
        _state.update({"match_id": None, "current": None})


def running_match() -> Optional[int]:
    return _state["match_id"]


def request_pause() -> None:
    _state["pause"] = True


def request_cancel() -> None:
    _state["cancel"] = True


def checkpoint() -> None:
    """Agents call this between units of work."""
    if _state["cancel"]:
        raise MatchStopped("cancelled")
    if _state["pause"]:
        raise MatchStopped("paused")


def set_progress(key: str, done: int, total: int) -> None:
    with _lock:
        _state["progress"][key] = {"done": done, "total": total}


def set_current(info: Optional[Dict[str, Any]]) -> None:
    _state["current"] = info


def snapshot() -> Dict[str, Any]:
    with _lock:
        return {"match_id": _state["match_id"], "progress": dict(_state["progress"]),
                "current": _state["current"], "pause_requested": _state["pause"]}


def emit(kind: str, text: str, match_id: Optional[int] = None) -> None:
    mid = match_id or _state["match_id"]
    if mid:
        store.add_event(mid, kind, text)


def _on_trace(event: Dict[str, Any]) -> None:
    mid = _state["match_id"]
    if not mid:
        return
    etype = event.get("type", "")
    if etype == "LLMCall":
        tt = str(event.get("task_type", ""))
        # Contender and router-mode calls already get an "Answer" / "Router"
        # line from their agents; only judge and Guardian calls add information.
        if tt.startswith("contestant_") or tt in TASK_TYPES:
            return
        kind = "Guardian" if tt.startswith("guardian") else "LLMCall"
        verdict = f" · {event['verdict'].lower()}" if event.get("verdict") else ""
        secs = (event.get("latency_ms") or 0) / 1000.0
        store.add_event(mid, kind, f"{event.get('model', '?')} · {tt} · {secs:.1f} s{verdict}")
    elif etype == "ShieldChain":
        checks = event.get("checks") or []
        flagged = [c["check"] for c in checks if c.get("status") in ("flag", "FLAG", "block", "BLOCK")]
        if event.get("blocked_by"):
            text = f"{event.get('gate', '')} · blocked by {event['blocked_by']}"
        elif flagged:
            text = f"{event.get('gate', '')} · flagged {', '.join(flagged)}"
        else:
            return  # clean passes would flood the ticker
        store.add_event(mid, "Shield", text)


register_trace_callback(_on_trace)
