# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
"""SuiteSquad agents: load the task suite, then screen every task prompt
with k9x_Shield and Granite Guardian before any contender sees it.

Guardian is mandatory and fails closed: if it is unreachable the match
stops. Each prompt is screened ONCE per Guardian model: verdicts are cached
by sha256(model + prompt), so later matches with the same suite reuse them
and only new or edited prompts are scanned. Adversarial tasks are EXPECTED
to be flagged — their verdicts are recorded, not used to drop them. Any
other task Guardian flags is excluded."""

from __future__ import annotations

import hashlib
from typing import Any, Dict

from k9_aif_abb.k9_governance.guardian_governance import GuardianGovernance

from arena import live, store
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
        store.update_match(match_id, phase="screening")
        guardian = GuardianGovernance(config=self.config)
        model = self.config.get("governance", {}).get("guardian", {}).get("model", "")
        tasks = store.get_tasks(match_id)
        excluded = cached = 0
        for i, task in enumerate(tasks, start=1):
            live.checkpoint()
            live.set_progress("screening", i - 1, len(tasks))
            if task.get("screen"):
                excluded += int(task["screen"].get("excluded", False))
                continue
            adversarial = task["type"] == "adversarial"
            key = hashlib.sha256(f"{model}\n{task['prompt']}".encode()).hexdigest()
            verdicts = store.get_cached_screen(key)
            hit = verdicts is not None
            if hit:
                cached += 1
            else:
                shield_reason = self.shield_ingress(task["prompt"])
                try:
                    guardian.pre_process({"query": task["prompt"]}, {"component": self.__class__.__name__})
                    guardian_verdict, guardian_reason = "safe", ""
                except PermissionError as exc:
                    if "unavailable" in str(exc).lower():
                        raise PermissionError(
                            "Granite Guardian is unavailable, and the arena never runs unscreened. "
                            f"Check the Guardian model on the Ollama host. ({exc})") from exc
                    guardian_verdict, guardian_reason = "flagged", str(exc)
                verdicts = {"shield": "blocked" if shield_reason else "pass", "shield_reason": shield_reason,
                            "guardian": guardian_verdict, "guardian_reason": guardian_reason}
                store.put_cached_screen(key, verdicts)
            shield_reason = verdicts.get("shield_reason")
            guardian_verdict, guardian_reason = verdicts["guardian"], verdicts.get("guardian_reason", "")
            exclude = (guardian_verdict == "flagged" or shield_reason is not None) and not adversarial
            screen = {**verdicts, "excluded": exclude, "cached": hit}
            store.set_task_screen(match_id, task["id"], screen)
            excluded += int(exclude)
            if exclude:
                live.emit("Guardian", f"{task['id']} excluded · {guardian_reason or shield_reason}")
            elif adversarial and (shield_reason or guardian_verdict == "flagged"):
                live.emit("Shield", f"{task['id']} flagged as expected (adversarial task)")
        live.set_progress("screening", len(tasks), len(tasks))
        if cached:
            live.emit("Guardian", f"{cached} of {len(tasks)} prompts already screened (cached)"
                      + (f"; {len(tasks) - cached} new" if cached < len(tasks) else ""))
        return {"screened": len(tasks), "cached": cached, "excluded": excluded}
