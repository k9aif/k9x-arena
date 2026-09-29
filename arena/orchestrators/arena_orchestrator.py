# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
"""ArenaOrchestrator: runs one match through SuiteSquad → ContestantSquad →
GradingSquad → ReportSquad. Knows its squads, never the router.

Each match has its own contenders, so the orchestrator builds the match's
inference config (one catalog entry per contender, plus the judges) and resets the framework's model factories before the
squads run. Only one match runs at a time (one GPU)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from k9_aif_abb.k9_agents.registry.agent_registry import AgentRegistry
from k9_aif_abb.k9_core.orchestration.base_orchestrator import BaseOrchestrator
from k9_aif_abb.k9_factories.llm_factory import LLMFactory
from k9_aif_abb.k9_factories.model_router_factory import ModelRouterFactory
from k9_aif_abb.k9_security.vulnerability.shield_governance import ShieldGovernance
from k9_aif_abb.k9_squad.squad_loader import SquadLoader

from arena import live, store
from arena.agents.common import agent_config
from arena.agents.contestant_agents import ForcedRunAgent
from arena.agents.grading_agents import (CodeGraderAgent, ExtractionGraderAgent, JudgeAgent,
                                         ReasoningGraderAgent, SafetyGraderAgent)
from arena.agents.report_agents import ConfigRecommenderAgent, RouterTestAgent, ScoringAgent
from arena.agents.suite_agents import InputScreenAgent, SuiteLoaderAgent
from arena.settings import build_inference_config

_SQUADS_YAML = Path(__file__).resolve().parent.parent / "squads" / "arena_squads.yaml"

_AGENTS = [SuiteLoaderAgent, InputScreenAgent, ForcedRunAgent, CodeGraderAgent,
           ExtractionGraderAgent, ReasoningGraderAgent, SafetyGraderAgent, JudgeAgent,
           ScoringAgent, RouterTestAgent, ConfigRecommenderAgent]

FULL_RUN = ["SuiteSquad", "ContestantSquad", "GradingSquad", "ReportSquad"]
RESCORE = ["ReportSquad"]


class ArenaOrchestrator(BaseOrchestrator):

    layer = "K9X Arena ArenaOrchestrator SBB"

    def __init__(self, config: Optional[Dict[str, Any]] = None, monitor=None, **kwargs):
        config = config or {}
        kwargs.setdefault("governance", ShieldGovernance(config=config))
        super().__init__(config, monitor=monitor, **kwargs)

    def _load_squads(self, match_config: Dict[str, Any], squad_ids: List[str]):
        registry = AgentRegistry()
        for cls in _AGENTS:
            registry.register(cls.__name__,
                              lambda c=cls: c(config=agent_config(c.__name__, match_config)))
        loader = SquadLoader(registry)
        return [loader.load_one(_SQUADS_YAML, sid) for sid in squad_ids]

    def execute_flow(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        match_id = int(payload["match_id"])
        squad_ids = payload.get("squads") or FULL_RUN
        match = store.get_match(match_id)
        if match is None:
            return {"status": "failed", "reason": f"match {match_id} not found"}

        match_config = build_inference_config(self.config, match["contenders"], match["judge"])
        LLMFactory.reset()
        ModelRouterFactory.reset()

        full = squad_ids == FULL_RUN
        live.begin(match_id)
        if full:
            store.update_match(match_id, status="running", error=None,
                               started_at=match.get("started_at") or time.time())
            live.emit("Match", f"started · {', '.join(match['contenders'])} · judge {match['judge']}")
        context: Dict[str, Any] = {"match_id": match_id}
        try:
            for squad in self._load_squads(match_config, squad_ids):
                context.update(squad.execute(context))
        except live.MatchStopped as stop:
            store.update_match(match_id, status="paused", phase=str(stop))
            live.emit("Match", f"{stop} · resume from the match page")
            return {"status": "paused", "reason": str(stop)}
        except Exception as exc:  # recorded for the UI, then re-raised for the logs
            store.update_match(match_id, status="failed", error=str(exc)[:1000])
            live.emit("Error", str(exc)[:200])
            raise
        finally:
            live.end()

        if full:
            store.update_match(match_id, status="completed", phase="completed", finished_at=time.time())
            live.emit("Match", "completed", match_id)
        return {"status": "completed", "match_id": match_id}

    def run(self, event: Dict[str, Any]) -> Dict[str, Any]:
        return self.execute_flow(event)
