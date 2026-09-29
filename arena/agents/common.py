# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
"""Shared base for arena agents plus the agent-YAML config loader.

Every arena agent runs with real governance (k9x_Shield by default), and
every model call goes through the framework's llm_invoke — never the router
or LLMFactory directly."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml

from k9_aif_abb.k9_core.agent.base_agent import BaseAgent
from k9_aif_abb.k9_core.orchestration.base_orchestrator import _run_coro_sync
from k9_aif_abb.k9_inference.models.inference_request import InferenceRequest
from k9_aif_abb.k9_security.vulnerability.shield_governance import ShieldGovernance
from k9_aif_abb.k9_utils.llm_invoke import llm_invoke

_YAML_DIR = Path(__file__).resolve().parent / "yaml"

SYSTEM_PROMPT = "You are a precise, helpful assistant. Follow the instructions exactly."

SUFFIX = {
    "code": "\n\nReturn only the Python code, in a single ```python code block.",
    "extraction": "\n\nReturn only one JSON object containing exactly the requested fields.",
    "reasoning": "\n\nWork it out step by step, then end with a final line of the form: Answer: <value>",
}


def contestant_prompt(task: Dict[str, Any]) -> str:
    return task["prompt"].rstrip() + SUFFIX.get(task["type"], "")


def _snake(name: str) -> str:
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s).lower()


def agent_config(agent_name: str, global_config: Dict[str, Any]) -> Dict[str, Any]:
    """agents/yaml/<snake>.yaml (role/goal) merged over the match config."""
    path = _YAML_DIR / f"{_snake(agent_name)}.yaml"
    local = yaml.safe_load(path.read_text()) if path.exists() else {}
    return {**global_config, **(local or {})}


class ArenaAgent(BaseAgent):
    """BaseAgent with k9x_Shield wired in and a timed llm_invoke helper."""

    layer = "K9X Arena Agent SBB"

    def __init__(self, config: Optional[Dict[str, Any]] = None, monitor=None, **kwargs):
        config = config or {}
        if kwargs.get("governance") is None:
            kwargs["governance"] = ShieldGovernance(config=config)
        super().__init__(config=config, monitor=monitor, **kwargs)

    def shield_ingress(self, text: str) -> Optional[str]:
        """Run k9x_Shield's ingress chain. Returns the block reason, or None."""
        try:
            _run_coro_sync(self.apply_pre_governance({"query": text}))
            return None
        except PermissionError as exc:
            return str(exc)

    def ask(self, prompt: str, task_type: str, system_prompt: str = SYSTEM_PROMPT) -> Tuple[str, str, int]:
        """(output, catalog alias chosen by the router, latency ms). Raises RuntimeError on failure."""
        arena = self.config.get("arena", {})
        req = InferenceRequest(
            prompt=prompt, system_prompt=system_prompt, task_type=task_type,
            metadata={"agent": self.__class__.__name__},
        )
        t0 = time.monotonic()
        resp = llm_invoke(self.config, req,
                          max_retries=int(arena.get("llm_retries", 2)),
                          retry_delay_s=float(arena.get("llm_retry_delay_s", 5)))
        return resp.output or "", resp.model_alias or "", int((time.monotonic() - t0) * 1000)
