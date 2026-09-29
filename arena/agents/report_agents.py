# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
"""ReportSquad agents: stars and leaderboard, router audit, recommended
router config. Pure computation over the store — rerun after reviews."""

from __future__ import annotations

from typing import Any, Dict

from arena import store
from arena.agents.common import ArenaAgent
from arena import scoring


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
        return {"rows": rows, "leaderboard": board,
                "judge": {"judged": len(judged), "disagreements": len(reviews),
                          "pending": sum(1 for r in reviews if r["status"] == "pending")}}


class RouterAuditAgent(ArenaAgent):
    layer = "K9X Arena RouterAuditAgent SBB"

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        match_id = payload["match_id"]
        rows = payload.get("scored", {}).get("rows", [])
        router_runs = store.get_runs(match_id, "router")
        alias_models = {r["alias"]: r["model"] for r in router_runs if r.get("alias")}
        audit = scoring.router_audit(store.get_tasks(match_id), router_runs, rows, alias_models)
        store.save_audit(match_id, audit)
        judged = [a for a in audit if a["verdict"] in ("match", "mismatch")]
        regrets = [max(0.0, a["regret"]) for a in judged]
        return {"audit": audit,
                "matches": sum(1 for a in judged if a["verdict"] == "match"),
                "types": len(judged),
                "avg_regret": round(sum(regrets) / len(regrets), 1) if regrets else None}


class ConfigRecommenderAgent(ArenaAgent):
    layer = "K9X Arena ConfigRecommenderAgent SBB"

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        match_id = payload["match_id"]
        scored = payload.get("scored", {})
        audit = payload.get("audited", {})
        yaml_text, notes = scoring.recommend_config(scored.get("rows", []))
        board = scored.get("leaderboard", [])
        report = {
            "leaderboard": board,
            "winner": board[0] if board else None,
            "judge": scored.get("judge", {}),
            "router": {"matches": audit.get("matches"), "types": audit.get("types"),
                       "avg_regret": audit.get("avg_regret")},
            "recommended_yaml": yaml_text,
            "recommended_notes": notes,
        }
        store.save_report(match_id, report)
        return report
