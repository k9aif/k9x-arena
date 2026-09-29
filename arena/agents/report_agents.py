# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
"""ReportSquad agents: stars and leaderboard, the held-out router test,
recommended router config. Pure computation over the store — rerun after
reviews."""

from __future__ import annotations

from typing import Any, Dict

from arena import live, router_eval, scoring, store
from arena.agents.common import ArenaAgent
from arena.settings import router_under_test


def _thresholds(config):
    return config.get("arena", {}).get("scoring", {}).get("stars", {"5": 90, "4": 75, "3": 60, "2": 40})


class ScoringAgent(ArenaAgent):
    layer = "K9X Arena ScoringAgent SBB"

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        match_id = payload["match_id"]
        store.update_match(match_id, phase="scoring")
        tasks = store.get_tasks(match_id)
        runs = store.get_runs(match_id)
        scores = scoring.final_run_scores(runs, store.get_grades(match_id), store.list_reviews(match_id))
        weights = self.config.get("arena", {}).get("scoring", {}).get("weights", {})
        rows = scoring.compute_stars(tasks, runs, scores, weights, _thresholds(self.config))
        store.save_stars(match_id, rows)
        board = scoring.leaderboard(rows, _thresholds(self.config))
        judged = [s for s in scores.values() if s["source"] in ("judge", "review")]
        reviews = store.list_reviews(match_id)
        match = store.get_match(match_id)
        families = (match.get("settings") or {}).get("families", {})
        judges = [match["judge"]] + ([match["settings"].get("judge_2")] if match["settings"].get("judge_2") else [])
        shared = sorted({families.get(c) for c in match["contenders"]}
                        & {families.get(j) for j in judges} - {None, ""})
        return {"rows": rows, "leaderboard": board,
                "judge": {"judged": len(judged), "disagreements": len(reviews),
                          "pending": sum(1 for r in reviews if r["status"] == "pending"),
                          "second_judge": match["settings"].get("judge_2") or None,
                          "shared_family": shared,
                          **scoring.judge_reliability(store.get_calibration(match_id),
                                                      [s["score"] for s in judged])}}


class RouterTestAgent(ArenaAgent):
    """Held-out test of K9ModelRouter's learned routing on this match's
    scores (arena/router_eval.py). No model is run: pass-1 scores are the
    ground truth, and route() makes no model call."""

    layer = "K9X Arena RouterTestAgent SBB"

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        match_id = payload["match_id"]
        match = store.get_match(match_id)
        if not match["router_mode"]:
            return {"available": False, "reason": "The router test was off for this match."}
        store.update_match(match_id, phase="router")
        tasks = store.get_tasks(match_id)
        runs = store.get_runs(match_id, "forced")
        scores = scoring.final_run_scores(runs, store.get_grades(match_id), store.list_reviews(match_id))
        quality = router_eval.task_quality(tasks, runs, scores)
        learning = self.config.get("arena", {}).get("router_test", {}).get("learning", {}) or {}
        result = router_eval.evaluate(tasks, quality, match["contenders"], router_under_test(self.config),
                                      learning=learning,
                                      progress=lambda i, n: live.set_progress("router", i, n))
        if result.get("available"):
            st = result["strategies"]
            live.emit("Router", f"held-out test · learned router {st['learned']['avg']} vs best single "
                                f"{st['single']['avg']} vs best possible {st['oracle']['avg']}")
        return result


class ConfigRecommenderAgent(ArenaAgent):
    layer = "K9X Arena ConfigRecommenderAgent SBB"

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        match_id = payload["match_id"]
        scored = payload.get("scored", {})
        test = payload.get("router_test", {}) or {}
        yaml_text, notes = scoring.recommend_config(scored.get("rows", []))
        board = scored.get("leaderboard", [])
        margin = float(self.config.get("arena", {}).get("scoring", {}).get("tie_margin", 2.0))
        how = scoring.verdict(board, margin)
        report = {
            "leaderboard": board,
            "winner": board[0] if board and how["kind"] != "draw" else None,
            "verdict": {**how, "margin": margin},
            "judge": scored.get("judge", {}),
            "router_test": test,
            "recommended_yaml": yaml_text,
            "recommended_notes": notes,
        }
        store.save_report(match_id, report)
        return report
