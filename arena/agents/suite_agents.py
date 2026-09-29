# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
"""SuiteSquad agents: load the task suite, then screen it before any
contender sees it.

Screening happens once per prompt (see arena/screening.py): built-in suites
are trusted and skip Granite Guardian; uploaded suites were scanned task by
task at upload and reuse those cached verdicts; any prompt without a verdict
is scanned now. Guardian is mandatory and fails closed. Adversarial tasks
are EXPECTED to be flagged; any other flagged task is excluded."""

from __future__ import annotations

from typing import Any, Dict

from k9_aif_abb.k9_governance.guardian_governance import GuardianGovernance

from arena import live, screening, store
from arena.agents.common import ArenaAgent
from arena.suites import load_suite


class SuiteLoaderAgent(ArenaAgent):
    layer = "K9X Arena SuiteLoaderAgent SBB"

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        match_id = payload["match_id"]
        match = store.get_match(match_id)
        if store.get_tasks(match_id):
            return {"loaded": len(store.get_tasks(match_id)), "resumed": True}
        suite, tasks = load_suite(match["suite"])
        store.save_tasks(match_id, tasks)
        store.update_match(match_id, suite_name=suite.get("name", match["suite"]))
        live.emit("Suite", f"{suite.get('name', match['suite'])} · {len(tasks)} tasks loaded")
        return {"loaded": len(tasks), "resumed": False}


class InputScreenAgent(ArenaAgent):
    layer = "K9X Arena InputScreenAgent SBB"

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        match_id = payload["match_id"]
        match = store.get_match(match_id)
        store.update_match(match_id, phase="screening")
        builtin = str(match["suite"]).startswith("built-in:")
        guardian = None if builtin else GuardianGovernance(config=self.config)
        shield = self.governance  # ShieldGovernance, wired by ArenaAgent
        tasks = store.get_tasks(match_id)
        excluded = cached = scanned = 0
        for i, task in enumerate(tasks, start=1):
            live.checkpoint()
            live.set_progress("screening", i - 1, len(tasks))
            if task.get("screen"):
                excluded += int(task["screen"].get("excluded", False))
                continue
            if builtin:
                verdicts, hit = screening.trusted_verdicts(self.config, task["prompt"], shield), False
            else:
                try:
                    verdicts, hit = screening.screen_prompt(self.config, task["prompt"], guardian, shield)
                except screening.GuardianUnavailable as exc:
                    raise PermissionError(str(exc)) from exc
                cached += int(hit)
                scanned += int(not hit)
            exclude = screening.excluded(task, verdicts)
            store.set_task_screen(match_id, task["id"], {**verdicts, "excluded": exclude, "cached": hit})
            excluded += int(exclude)
            if exclude:
                live.emit("Guardian", f"{task['id']} excluded · {verdicts.get('guardian_reason') or verdicts.get('shield_reason')}")
            elif task["type"] == "adversarial" and (verdicts.get("shield_reason") or verdicts["guardian"] == "flagged"):
                live.emit("Shield", f"{task['id']} flagged as expected (adversarial task)")
        live.set_progress("screening", len(tasks), len(tasks))
        if builtin:
            live.emit("Guardian", f"built-in suite · trusted, no Guardian scan ({len(tasks)} prompts; Shield still checks each call)")
        elif cached:
            live.emit("Guardian", f"{cached} of {len(tasks)} prompts already screened"
                      + (f"; {scanned} scanned now" if scanned else ""))
        return {"screened": len(tasks), "cached": cached, "scanned": scanned, "excluded": excluded,
                "trusted": builtin}
