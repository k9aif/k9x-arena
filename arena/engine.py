# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
"""Runs matches in a background thread — one at a time (one GPU) — by
sending events to ArenaRouter. Nothing here calls a model."""

from __future__ import annotations

import logging
import threading
from typing import Optional

from arena import live, store
from arena.router.arena_router import get_router

log = logging.getLogger("arena.engine")
_thread: Optional[threading.Thread] = None
_lock = threading.Lock()


class Busy(RuntimeError):
    pass


def recover_after_restart() -> None:
    """A match left 'running' by a stopped server can be resumed."""
    for m in store.list_matches(500):
        if m["status"] in ("running", "queued"):
            store.update_match(m["id"], status="paused", phase="server restarted")


def is_busy() -> bool:
    return _thread is not None and _thread.is_alive()


def start(match_id: int) -> None:
    global _thread
    with _lock:
        if is_busy():
            raise Busy(f"match {live.running_match()} is still running")

        def _run():
            try:
                get_router().route("arena.run", {"match_id": match_id})
            except Exception:
                log.exception("match %s failed", match_id)

        _thread = threading.Thread(target=_run, name=f"arena-match-{match_id}", daemon=True)
        _thread.start()


def rescore(match_id: int) -> dict:
    return get_router().route("arena.rescore", {"match_id": match_id})
