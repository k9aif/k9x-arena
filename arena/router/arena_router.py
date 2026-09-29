# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
"""ArenaRouter: the single entry point. Resolves event_type → orchestrator
and runs it in-process (no Kafka), like K9X Studio's K9EventRouter.

    arena.run      → ArenaOrchestrator, all four squads (also resumes)
    arena.rescore  → ArenaOrchestrator, ReportSquad only (after reviews)"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, Optional

from k9_aif_abb.k9_core.router.base_router import BaseRouter
from k9_aif_abb.k9_security.vulnerability.shield_governance import ShieldGovernance

from arena.orchestrators.arena_orchestrator import FULL_RUN, RESCORE, ArenaOrchestrator
from arena.settings import load_config


class ArenaRouter(BaseRouter):
    layer = "K9X Arena ArenaRouter SBB"

    _SQUADS = {"arena.run": FULL_RUN, "arena.rescore": RESCORE}

    def __init__(self, config: Optional[Dict[str, Any]] = None, **kwargs):
        config = config or {}
        kwargs.setdefault("governance", ShieldGovernance(config=config))
        super().__init__(config, **kwargs)

    def route(self, event_type: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        key = (event_type or "").strip().lower()
        orchestrator = self.registry.get(key)
        if orchestrator is None:
            raise KeyError(f"[{self.layer}] no orchestrator for event_type={event_type!r}")
        event = {**(payload or {}), "event_type": key, "squads": self._SQUADS.get(key)}
        return orchestrator.run(event)


@lru_cache(maxsize=1)
def get_router() -> ArenaRouter:
    config = load_config()
    router = ArenaRouter(config=config)
    orchestrator = ArenaOrchestrator(config=config)
    for event_type in ArenaRouter._SQUADS:
        router.register_orchestrator(event_type, orchestrator)
    return router
