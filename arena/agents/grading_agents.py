# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
"""GradingSquad agents. Deterministic graders first (no GPU), then Granite
Guardian's safety pass over adversarial answers, then the LLM judge for
open-ended answers — each phase batched so the GPU swaps models once."""

from __future__ import annotations

import re
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
            live.set_current({"model": "guardian", "task_id": task["id"], "title": task.get("title", ""),
                              "type": task["type"], "run_no": run["run_no"], "for_model": run["model"]})
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
        live.set_current(None)
        return {"graded": len(pending)}


_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

# Deliberately poor answers the judge must score LOW (calibration).
PLANTED = {
    "summarization": "This document is about insurance and contains some information.",
    "chat": "Please check your policy documents or contact us.",
}


def judged_text(output: str) -> str:
    """Hidden reasoning is removed so a model is neither rewarded nor penalised for it."""
    return _THINK.sub("", output or "").strip()


class JudgeAgent(ArenaAgent):
    """Fair judging for open-ended answers, with the framework's
    K9PromptEvaluator (A–F):
      - anonymized: the judge never sees which model wrote an answer;
      - independent: pass 1 uses the `judge` entry, pass 2 the `judge_b`
        entry (a second judge model, or the same judge resampled), so the
        passes can genuinely disagree; disagreements go to review;
      - calibrated: per task the judge also grades a planted poor answer,
        and Results warns when it failed to score those low;
      - hidden <think> reasoning is stripped before judging."""

    layer = "K9X Arena JudgeAgent SBB"

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        match_id = payload["match_id"]
        store.update_match(match_id, phase="judging")
        judging = self.config.get("arena", {}).get("judging", {})
        passes = max(1, min(2, int(judging.get("passes", 2))))
        tolerance = int(judging.get("review_on_disagreement_grades", 1))
        evaluators = [EvaluationFactory.create({**self.config, "judge_model": "judge"}),
                      EvaluationFactory.create({**self.config, "judge_model": "judge_b"})]
        pending = list(_ungraded(match_id, ["summarization", "chat"],
                                 [f"judge_{p}" for p in range(1, passes + 1)]))
        disagreements = 0
        for i, (run, task) in enumerate(pending, start=1):
            live.checkpoint()
            live.set_progress("judging", i - 1, len(pending))
            live.set_current({"model": "judge", "task_id": task["id"], "title": task.get("title", ""),
                              "type": task["type"], "run_no": run["run_no"]})
            expected = task.get("reference") or task.get("rubric") or ""
            answer = judged_text(run["output"])
            results = []
            for p in range(1, passes + 1):
                try:
                    res = evaluators[p - 1].evaluate(prompt=task["prompt"], input_data={}, actual_output=answer,
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
        calibrated = self._calibrate(match_id, evaluators[0]) if judging.get("calibration", True) else 0
        live.set_progress("judging", len(pending), len(pending))
        live.set_current(None)
        return {"judged": len(pending), "disagreements": disagreements, "calibrated": calibrated}

    def _calibrate(self, match_id: int, evaluator) -> int:
        done = {c["task_id"] for c in store.get_calibration(match_id)}
        tasks = [t for t in store.get_tasks(match_id)
                 if t["type"] in PLANTED and t["id"] not in done and not (t.get("screen") or {}).get("excluded")]
        for task in tasks:
            live.checkpoint()
            planted = PLANTED[task["type"]]
            try:
                res = evaluator.evaluate(prompt=task["prompt"], input_data={}, actual_output=planted,
                                         expected=task.get("reference") or task.get("rubric") or "",
                                         test_case_description=task.get("title", ""))
            except RuntimeError:
                continue
            store.save_calibration(match_id, task["id"], planted, res.score, res.grade)
        if tasks:
            cal = store.get_calibration(match_id)
            avg = sum(c["score"] for c in cal) / len(cal)
            live.emit("Judge", f"calibration · planted poor answers scored {avg:.0f} on average")
        return len(tasks)
