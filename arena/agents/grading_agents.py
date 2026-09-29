# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
"""GradingSquad agents. Deterministic graders first (no GPU), then Granite
Guardian's safety pass over adversarial answers, then the LLM judge for
open-ended answers — each phase batched so the GPU swaps models once."""

from __future__ import annotations

from typing import Any, Dict, List

from k9_aif_abb.k9_factories.evaluation_factory import EvaluationFactory
from k9_aif_abb.k9_governance.guardian_governance import GuardianGovernance

from arena import graders, live, store
from arena.agents.common import ArenaAgent
from arena.scoring import grade_distance


def _ungraded(match_id: int, task_types: List[str], graders_done: List[str]):
    tasks = {t["id"]: t for t in store.get_tasks(match_id)}
    done = {(g["run_id"], g["grader"]) for g in store.get_grades(match_id)}
    for run in store.get_runs(match_id, "forced"):
        task = tasks.get(run["task_id"])
        if not task or task["type"] not in task_types or run.get("error"):
            continue
        if all((run["id"], g) in done for g in graders_done):
            continue
        yield run, task


class CodeGraderAgent(ArenaAgent):
    layer = "K9X Arena CodeGraderAgent SBB"

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        match_id = payload["match_id"]
        store.update_match(match_id, phase="grading")
        timeout = int(self.config.get("arena", {}).get("sandbox", {}).get("code_timeout_seconds", 20))
        n = 0
        for run, task in _ungraded(match_id, ["code"], ["code"]):
            live.checkpoint()
            score, detail = graders.grade_code(run["output"], task["tests"], timeout=timeout)
            store.save_grade(run["id"], "code", score, detail=detail)
            n += 1
        return {"graded": n}


class ExtractionGraderAgent(ArenaAgent):
    layer = "K9X Arena ExtractionGraderAgent SBB"

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        n = 0
        for run, task in _ungraded(payload["match_id"], ["extraction"], ["extraction"]):
            score, detail = graders.grade_extraction(run["output"], task["expected"])
            store.save_grade(run["id"], "extraction", score, detail=detail)
            n += 1
        return {"graded": n}


class ReasoningGraderAgent(ArenaAgent):
    layer = "K9X Arena ReasoningGraderAgent SBB"

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        n = 0
        for run, task in _ungraded(payload["match_id"], ["reasoning"], ["reasoning"]):
            score, detail = graders.grade_reasoning(run["output"], str(task["answer"]), task.get("aliases"))
            store.save_grade(run["id"], "reasoning", score, detail=detail)
            n += 1
        return {"graded": n}


class SafetyGraderAgent(ArenaAgent):
    layer = "K9X Arena SafetyGraderAgent SBB"

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        match_id = payload["match_id"]
        guardian = GuardianGovernance(config=self.config)
        pending = list(_ungraded(match_id, ["adversarial"], ["safety"]))
        for i, (run, task) in enumerate(pending, start=1):
            live.checkpoint()
            live.set_progress("safety", i - 1, len(pending))
            unsafe = False
            try:
                guardian.post_process({"output": run["output"]}, {"component": self.__class__.__name__})
            except PermissionError as exc:
                if "unavailable" in str(exc).lower():
                    raise PermissionError(f"Granite Guardian is unavailable; grading stopped. ({exc})") from exc
                unsafe = True
            score, detail = graders.grade_adversarial(run["output"], task["expect"],
                                                      task.get("must_not_contain"), guardian_unsafe=unsafe)
            store.save_grade(run["id"], "safety", score, detail=detail)
        live.set_progress("safety", len(pending), len(pending))
        return {"graded": len(pending)}


class JudgeAgent(ArenaAgent):
    """Two independent passes of the framework's K9PromptEvaluator (LLM-as-
    judge, A–F), routed to the `judge` catalog entry — never a contender.
    Answers are judged without the model's name. Passes more than
    `review_on_disagreement_grades` apart go to the review queue."""

    layer = "K9X Arena JudgeAgent SBB"

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        match_id = payload["match_id"]
        store.update_match(match_id, phase="judging")
        judging = self.config.get("arena", {}).get("judging", {})
        passes = max(1, int(judging.get("passes", 2)))
        tolerance = int(judging.get("review_on_disagreement_grades", 1))
        evaluator = EvaluationFactory.create({**self.config, "judge_model": "judge"})
        pending = list(_ungraded(match_id, ["summarization", "chat"],
                                 [f"judge_{p}" for p in range(1, passes + 1)]))
        disagreements = 0
        for i, (run, task) in enumerate(pending, start=1):
            live.checkpoint()
            live.set_progress("judging", i - 1, len(pending))
            live.set_current({"model": "judge", "task_id": task["id"], "title": task.get("title", ""),
                              "type": task["type"], "run_no": run["run_no"]})
            expected = task.get("reference") or task.get("rubric") or ""
            results = []
            for p in range(1, passes + 1):
                try:
                    res = evaluator.evaluate(prompt=task["prompt"], input_data={}, actual_output=run["output"],
                                             expected=expected, test_case_description=task.get("title", ""))
                except RuntimeError as exc:
                    live.emit("Error", f"judge failed on {task['id']}: {str(exc)[:120]}")
                    break
                store.save_grade(run["id"], f"judge_{p}", res.score, res.grade,
                                 {"rationale": res.rationale, "verdict": res.verdict,
                                  "dimensions": [{"name": d.name, "score": d.score} for d in res.dimensions]})
                results.append(res)
            if len(results) >= 2 and grade_distance(results[0].grade, results[1].grade) > tolerance:
                disagreements += 1
                store.add_review(match_id, run["id"],
                                 f"judges disagree: {results[0].grade} vs {results[1].grade}")
                live.emit("Review", f"{task['id']} · judges disagree ({results[0].grade} vs {results[1].grade})")
        live.set_progress("judging", len(pending), len(pending))
        live.set_current(None)
        return {"judged": len(pending), "disagreements": disagreements}
