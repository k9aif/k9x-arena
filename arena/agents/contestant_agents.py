# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
"""ContestantSquad agents.

ForcedRunAgent pins each task to one contender by sending the contender's
unique catalog capability (task_type="contestant_<n>") through llm_invoke,
so the router itself routes it — agents never bypass the router. Calls are
grouped by model (every run of every task for one model, then the next) so
the single GPU swaps models as rarely as possible.

RouterRunAgent sends each task once with its REAL task type and records
which catalog entry K9ModelRouter chose — the evidence for the router audit."""

from __future__ import annotations

from typing import Any, Dict

from arena import graders, live, store
from arena.agents.common import ArenaAgent, contestant_prompt
from arena.graders import is_refusal

# Graded the moment an answer lands (no GPU needed), so live lanes show
# scores during the match. Judged and safety-graded types wait for their
# batched phases so the GPU doesn't swap models mid-contest.
_INSTANT = {"code", "extraction", "reasoning"}


def grade_now(run_id: int, task: Dict[str, Any], output: str, timeout: int) -> None:
    if task["type"] == "code":
        score, detail = graders.grade_code(output, task["tests"], timeout=timeout)
    elif task["type"] == "extraction":
        score, detail = graders.grade_extraction(output, task["expected"])
    else:
        score, detail = graders.grade_reasoning(output, str(task["answer"]), task.get("aliases"))
    store.save_grade(run_id, task["type"], score, detail=detail)
from arena.settings import alias_for, model_for_alias


def _active_tasks(match_id: int):
    return [t for t in store.get_tasks(match_id) if not (t.get("screen") or {}).get("excluded")]


class ForcedRunAgent(ArenaAgent):
    layer = "K9X Arena ForcedRunAgent SBB"

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        match_id = payload["match_id"]
        match = store.get_match(match_id)
        store.update_match(match_id, phase="answering")
        tasks = _active_tasks(match_id)
        runs = match["runs_per_task"]
        timeout = int(self.config.get("arena", {}).get("sandbox", {}).get("code_timeout_seconds", 20))
        total = len(match["contenders"]) * len(tasks) * runs
        done = len(store.get_runs(match_id, "forced"))
        previous_model = None
        for i, model in enumerate(match["contenders"]):
            alias = alias_for(i)
            for run_no in range(1, runs + 1):
                for task in tasks:
                    live.checkpoint()
                    if store.run_exists(match_id, "forced", model, task["id"], run_no):
                        continue
                    if previous_model and previous_model != model:
                        live.emit("GPU", f"swap · {previous_model} → {model}")
                    previous_model = model
                    live.set_current({"model": model, "task_id": task["id"], "title": task.get("title", ""),
                                      "type": task["type"], "run_no": run_no})
                    prompt = contestant_prompt(task)
                    output, error, latency = "", None, 0
                    blocked = None if task["type"] == "adversarial" else self.shield_ingress(prompt)
                    if blocked:
                        error = blocked
                    else:
                        try:
                            output, _, latency = self.ask(prompt, task_type=alias)
                        except RuntimeError as exc:
                            error = str(exc)[:500]
                    run_id = store.save_run(match_id, "forced", model, alias, task["id"], run_no,
                                            output, latency, is_refusal(output), error)
                    if not error and task["type"] in _INSTANT:
                        grade_now(run_id, task, output, timeout)
                    done += 1
                    live.set_progress("answering", done, total)
                    live.emit("Answer" if not error else "Error",
                              f"{model} · {task['id']} · run {run_no} · "
                              + (f"{latency / 1000:.1f} s" if not error else error[:120]))
        live.set_current(None)
        return {"answers": done, "total": total}


class RouterRunAgent(ArenaAgent):
    layer = "K9X Arena RouterRunAgent SBB"

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        match_id = payload["match_id"]
        match = store.get_match(match_id)
        if not match["router_mode"]:
            return {"skipped": True}
        store.update_match(match_id, phase="router")
        tasks = sorted(_active_tasks(match_id), key=lambda t: t["type"])  # group by type → fewer swaps
        existing = {r["task_id"] for r in store.get_runs(match_id, "router")}
        done = len(existing)
        for task in tasks:
            live.checkpoint()
            live.set_progress("router", done, len(tasks))
            if task["id"] in existing:
                continue
            live.set_current({"model": "router", "task_id": task["id"], "title": task.get("title", ""),
                              "type": task["type"], "run_no": 1})
            prompt = contestant_prompt(task)
            output, alias, latency, error = "", "", 0, None
            try:
                output, alias, latency = self.ask(prompt, task_type=task["type"])
            except RuntimeError as exc:
                error = str(exc)[:500]
            model = model_for_alias(self.config, alias) if alias else "?"
            store.save_run(match_id, "router", model, alias, task["id"], 1, output, latency, is_refusal(output), error)
            done += 1
            live.emit("Router", f"task_type={task['type']} → {alias or '?'} ({model})")
        live.set_progress("router", len(tasks), len(tasks))
        live.set_current(None)
        return {"routed": done}
