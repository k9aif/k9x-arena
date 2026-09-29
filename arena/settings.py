# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
"""Configuration for K9X Arena.

`.env` says *where* and *which* (Ollama host, model tags, logins);
`config.yaml` says *how* (runs, weights, thresholds). `config.yaml` is read
through the framework's `load_yaml`, which expands ``${VAR:-default}``.

The inference section is NOT static: every match has its own contenders,
so `build_inference_config()` builds the router's catalog per match:

    contestant_<n>  capability contestant_<n>   (forced mode pins one model)
    judge           capability judge            (never a contender)
    general / reasoning                         (router under test)
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv

from k9_aif_abb.k9_utils.config_loader import load_yaml

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

TASK_TYPES = ["code", "extraction", "reasoning", "summarization", "chat", "adversarial"]


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def load_config() -> Dict[str, Any]:
    cfg = load_yaml(ROOT / "config.yaml")
    arena = cfg.setdefault("arena", {})
    arena["runs_per_task"] = int(arena.get("runs_per_task", 3))
    arena["router_mode"] = _truthy(arena.get("router_mode", True))
    # Granite Guardian is mandatory in the arena: whatever config.yaml says,
    # it is on and fails closed.
    guardian = cfg.setdefault("governance", {}).setdefault("guardian", {})
    guardian["enabled"] = True
    guardian["on_unavailable"] = "fail_closed"
    guardian.setdefault("model", "granite4.1-guardian:8b")
    cfg.setdefault("ollama", {})["base_url"] = ollama_base_url()
    return cfg


def ollama_base_url() -> str:
    return os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")


def split_tags(value: str) -> List[str]:
    return [t.strip() for t in (value or "").split(",") if t.strip()]


def default_contestants(cfg: Dict[str, Any]) -> List[str]:
    return split_tags(str(cfg["arena"].get("contestants", "")))


def judge_model(cfg: Dict[str, Any]) -> str:
    return str(cfg["arena"].get("judge_model", "")).strip()


def router_under_test(cfg: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """The production-style catalog entries router mode is audited against."""
    rut = cfg["arena"].get("router_under_test", {}) or {}
    out = {}
    for alias in ("general", "reasoning"):
        entry = rut.get(alias, {}) or {}
        out[alias] = {
            "model": str(entry.get("model", "")).strip(),
            "capabilities": list(entry.get("capabilities", [])),
        }
    return out


def alias_for(index: int) -> str:
    return f"contestant_{index + 1}"


def build_inference_config(cfg: Dict[str, Any], contestants: List[str], judge: str) -> Dict[str, Any]:
    """A full app config whose inference section pins each model of THIS
    match to its own catalog entry. Agents still only call llm_invoke."""
    max_tokens = int(cfg["arena"].get("max_tokens", 2048))

    def model_entry(tag: str) -> Dict[str, Any]:
        return {"model": tag, "temperature": 0.2, "max_tokens": max_tokens}

    llm_models: Dict[str, Any] = {}
    catalog: Dict[str, Any] = {}
    for i, tag in enumerate(contestants):
        alias = alias_for(i)
        llm_models[alias] = model_entry(tag)
        catalog[alias] = {"provider": "ollama", "llm_ref": alias, "capabilities": [alias]}
    llm_models["judge"] = {**model_entry(judge), "temperature": 0.0}
    catalog["judge"] = {"provider": "ollama", "llm_ref": "judge", "capabilities": ["judge"]}

    rut = router_under_test(cfg)
    for alias, entry in rut.items():
        tag = entry["model"] or (contestants[0] if contestants else judge)
        llm_models[alias] = model_entry(tag)
        catalog[alias] = {"provider": "ollama", "llm_ref": alias, "capabilities": entry["capabilities"]}

    inference = {
        "router": {
            "type": "k9_model_router",
            "default_model": "general",
            "persistence": {
                "enabled": True,
                "provider": "sqlite",
                "sqlite": {"db_path": str(ROOT / "runtime" / "k9_model_router.db")},
            },
        },
        "llm_factory": {
            "backend": "ollama",
            "provider": "ollama",
            "base_url": ollama_base_url(),
            "models": llm_models,
        },
        "model_catalog": {"default_model": "general", "models": catalog},
    }
    return {**cfg, "inference": inference}


def model_for_alias(match_config: Dict[str, Any], alias: str) -> str:
    entry = match_config["inference"]["llm_factory"]["models"].get(alias) or {}
    return entry.get("model", alias) if isinstance(entry, dict) else str(entry)


def credentials() -> Dict[str, Dict[str, str]]:
    users = {
        os.environ.get("ARENA_USER", "demo"): {
            "password": os.environ.get("ARENA_PASSWORD", "demo"), "role": "demo"},
    }
    admin_pw = os.environ.get("ARENA_ADMIN_PASSWORD", "")
    if admin_pw:
        users[os.environ.get("ARENA_ADMIN_USER", "admin")] = {"password": admin_pw, "role": "admin"}
    return users


def db_path() -> Path:
    p = Path(os.environ.get("ARENA_DB_PATH", str(ROOT / "runtime" / "arena.db")))
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def safe_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
