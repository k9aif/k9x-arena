# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
"""Prompt screening, done once per prompt.

- Built-in suites ship with the arena and are trusted: no Guardian scan at
  match time (k9x_Shield still checks every contender call).
- Uploaded suites are scanned task by task at upload time; verdicts are
  cached by sha256(guardian model + prompt), so a match reuses them.
- A prompt with no cached verdict is scanned when first used.
Guardian is mandatory and fails closed: unavailable raises PermissionError."""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional, Tuple

from k9_aif_abb.k9_governance.guardian_governance import GuardianGovernance
from k9_aif_abb.k9_security.vulnerability.shield_governance import ShieldGovernance

from arena import store


class GuardianUnavailable(PermissionError):
    pass


def cache_key(guardian_model: str, prompt: str) -> str:
    return hashlib.sha256(f"{guardian_model}\n{prompt}".encode()).hexdigest()


def shield_reason(shield: ShieldGovernance, prompt: str) -> Optional[str]:
    try:
        shield.pre_process({"query": prompt}, {"component": "ArenaScreening"})
        return None
    except PermissionError as exc:
        return str(exc)


def screen_prompt(config: Dict[str, Any], prompt: str, guardian: Optional[GuardianGovernance] = None,
                  shield: Optional[ShieldGovernance] = None) -> Tuple[Dict[str, Any], bool]:
    """(verdicts, from_cache). Scans only on a cache miss."""
    model = config.get("governance", {}).get("guardian", {}).get("model", "")
    key = cache_key(model, prompt)
    cached = store.get_cached_screen(key)
    if cached is not None:
        return cached, True
    guardian = guardian or GuardianGovernance(config=config)
    shield = shield or ShieldGovernance(config=config)
    reason = shield_reason(shield, prompt)
    try:
        guardian.pre_process({"query": prompt}, {"component": "ArenaScreening"})
        verdict, g_reason = "safe", ""
    except PermissionError as exc:
        if "unavailable" in str(exc).lower():
            raise GuardianUnavailable(
                f"Granite Guardian is unavailable, and the arena never runs unscreened. ({exc})") from exc
        verdict, g_reason = "flagged", str(exc)
    verdicts = {"shield": "blocked" if reason else "pass", "shield_reason": reason,
                "guardian": verdict, "guardian_reason": g_reason}
    store.put_cached_screen(key, verdicts)
    return verdicts, False


def trusted_verdicts(config: Dict[str, Any], prompt: str, shield: Optional[ShieldGovernance] = None) -> Dict[str, Any]:
    """Built-in suite: no Guardian call; the cheap Shield check still runs."""
    reason = shield_reason(shield or ShieldGovernance(config=config), prompt)
    return {"shield": "blocked" if reason else "pass", "shield_reason": reason,
            "guardian": "trusted", "guardian_reason": "built-in suite"}


def excluded(task: Dict[str, Any], verdicts: Dict[str, Any]) -> bool:
    """Adversarial tasks are expected to be flagged; any other flagged task is dropped."""
    if task["type"] == "adversarial":
        return False
    return verdicts.get("guardian") == "flagged" or bool(verdicts.get("shield_reason"))
