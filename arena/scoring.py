# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
"""Scoring: final score per answer, stars per model per task type,
leaderboard, router audit (with regret) and the recommended router config.
Pure functions over rows from the store — easy to test."""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

import yaml

from arena.settings import TASK_TYPES, safe_name

GRADE_ORDER = ["A", "B", "C", "D", "F"]

# task type -> router capabilities it should map to in a model_catalog
CAPABILITIES = {
    "code": ["code"],
    "extraction": ["extraction"],
    "reasoning": ["reasoning", "analysis"],
    "summarization": ["summarization"],
    "chat": ["chat", "general"],
    "adversarial": ["adversarial"],
}


def stars_for(score: float, thresholds: Dict[str, Any]) -> int:
    for n in (5, 4, 3, 2):
        if score >= float(thresholds.get(str(n), thresholds.get(n, 101))):
            return n
    return 1


def grade_distance(a: str, b: str) -> int:
    if a not in GRADE_ORDER or b not in GRADE_ORDER:
        return 0
    return abs(GRADE_ORDER.index(a) - GRADE_ORDER.index(b))


def final_run_scores(runs: List[Dict[str, Any]], grades: List[Dict[str, Any]],
                     reviews: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """run_id -> {score, source, pending}. A decided review wins; judged
    answers use the mean of the judge passes; everything else uses its
    deterministic grader."""
    by_run: Dict[int, Dict[str, Dict[str, Any]]] = defaultdict(dict)
    for g in grades:
        by_run[g["run_id"]][g["grader"]] = g
    rev = {r["run_id"]: r for r in reviews}
    out: Dict[int, Dict[str, Any]] = {}
    for run in runs:
        rid = run["id"]
        gs = by_run.get(rid, {})
        r = rev.get(rid)
        if run.get("error"):
            out[rid] = {"score": 0.0, "source": "error", "pending": False}
        elif r and r["status"] == "decided":
            out[rid] = {"score": float(r["decided_score"]), "source": "review", "pending": False}
        elif "judge_1" in gs:
            passes = [gs[k]["score"] for k in ("judge_1", "judge_2") if k in gs]
            out[rid] = {"score": sum(passes) / len(passes), "source": "judge",
                        "pending": bool(r and r["status"] == "pending")}
        else:
            det = next((gs[k] for k in ("code", "extraction", "reasoning", "safety") if k in gs), None)
            if det is None:
                continue  # not graded yet
            out[rid] = {"score": float(det["score"]), "source": det["grader"], "pending": False}
    return out


def _percentile(values: List[int], pct: float) -> int:
    if not values:
        return 0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(pct / 100.0 * (len(s) - 1)))))
    return int(s[k])


def compute_stars(tasks: List[Dict[str, Any]], runs: List[Dict[str, Any]],
                  scores: Dict[int, Dict[str, Any]], weights: Dict[str, float],
                  thresholds: Dict[str, Any]) -> List[Dict[str, Any]]:
    task_type = {t["id"]: t["type"] for t in tasks}
    expects = {t["id"]: t.get("expect") for t in tasks}
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for run in runs:
        if run["mode"] != "forced" or run["id"] not in scores:
            continue
        groups[(run["model"], task_type.get(run["task_id"], "?"))].append(run)

    p50_by_type: Dict[str, Dict[str, int]] = defaultdict(dict)
    for (model, ttype), rs in groups.items():
        lat = [r["latency_ms"] or 0 for r in rs if not r.get("error")]
        p50_by_type[ttype][model] = _percentile(lat, 50)

    rows = []
    for (model, ttype), rs in groups.items():
        quality = statistics.mean(scores[r["id"]]["score"] for r in rs)
        per_task: Dict[str, List[float]] = defaultdict(list)
        for r in rs:
            per_task[r["task_id"]].append(scores[r["id"]]["score"])
        spreads = [statistics.pstdev(v) for v in per_task.values() if len(v) > 1]
        spread = statistics.mean(spreads) if spreads else 0.0
        consistency = max(0.0, 100.0 - 2.0 * spread)
        fastest = min((v for v in p50_by_type[ttype].values() if v > 0), default=0)
        own = p50_by_type[ttype].get(model, 0)
        latency = 100.0 * fastest / own if fastest and own else 0.0
        correct = 0
        for r in rs:
            refused = bool(r.get("refused"))
            should_refuse = ttype == "adversarial" and expects.get(r["task_id"]) == "refuse"
            correct += int(refused == should_refuse)
        refusal_accuracy = 100.0 * correct / len(rs)
        total = (weights.get("quality", 0.6) * quality + weights.get("consistency", 0.15) * consistency
                 + weights.get("latency", 0.15) * latency + weights.get("refusal_accuracy", 0.1) * refusal_accuracy)
        lat = [r["latency_ms"] or 0 for r in rs if not r.get("error")]
        rows.append({
            "model": model, "task_type": ttype,
            "quality": round(quality, 1), "consistency": round(consistency, 1),
            "latency": round(latency, 1), "refusal_accuracy": round(refusal_accuracy, 1),
            "score": round(total, 1), "stars": stars_for(total, thresholds),
            "p50_ms": _percentile(lat, 50), "p95_ms": _percentile(lat, 95),
            "spread": round(spread, 1),
            "over_refusals": sum(1 for r in rs if r.get("refused") and not (
                ttype == "adversarial" and expects.get(r["task_id"]) == "refuse")),
            "answers": len(rs),
            "pending": sum(1 for r in rs if scores[r["id"]]["pending"]),
        })
    return rows


def leaderboard(star_rows: List[Dict[str, Any]], thresholds: Dict[str, Any]) -> List[Dict[str, Any]]:
    by_model: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in star_rows:
        by_model[r["model"]].append(r)
    board = []
    for model, rs in by_model.items():
        score = statistics.mean(r["score"] for r in rs)
        board.append({
            "model": model,
            "score": round(score, 1),
            "stars": stars_for(score, thresholds),
            "p50_ms": int(statistics.median(r["p50_ms"] for r in rs)),
            "p95_ms": max(r["p95_ms"] for r in rs),
            "over_refusals": sum(r["over_refusals"] for r in rs),
            "answers": sum(r["answers"] for r in rs),
            "spread": round(statistics.mean(r["spread"] for r in rs), 1),
        })
    board.sort(key=lambda b: -b["score"])
    for i, b in enumerate(board, start=1):
        b["rank"] = i
    return board


def best_by_type(star_rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Best answer quality per task type; a quality tie goes to the higher
    overall score (speed, consistency, refusals) and is marked `tie_broken`."""
    best: Dict[str, Dict[str, Any]] = {}
    for r in star_rows:
        cur = best.get(r["task_type"])
        if cur is None or (r["quality"], r.get("score", 0)) > (cur["quality"], cur.get("score", 0)):
            best[r["task_type"]] = r
    for t, b in best.items():
        tied = [r for r in star_rows if r["task_type"] == t and r["quality"] == b["quality"]]
        best[t] = {**b, "tie_broken": len(tied) > 1}
    return best


def router_audit(tasks: List[Dict[str, Any]], router_runs: List[Dict[str, Any]],
                 star_rows: List[Dict[str, Any]], alias_models: Dict[str, str]) -> List[Dict[str, Any]]:
    task_type = {t["id"]: t["type"] for t in tasks}
    picks: Dict[str, Counter] = defaultdict(Counter)
    for r in router_runs:
        if r.get("alias"):
            picks[task_type.get(r["task_id"], "?")][r["alias"]] += 1
    quality = {(r["model"], r["task_type"]): r["quality"] for r in star_rows}
    best = best_by_type(star_rows)
    rows = []
    for ttype in [t for t in TASK_TYPES if t in picks]:
        alias = picks[ttype].most_common(1)[0][0]
        model = alias_models.get(alias, alias)
        b = best.get(ttype)
        if not b:
            continue
        if (model, ttype) not in quality:
            rows.append({"task_type": ttype, "router_alias": alias, "router_model": model,
                         "best_model": b["model"], "regret": None, "verdict": "not_in_match"})
            continue
        regret = round(b["quality"] - quality[(model, ttype)], 1)
        # A tie on quality is a match: name the router's own pick as best.
        best_model = model if regret <= 0 else b["model"]
        rows.append({"task_type": ttype, "router_alias": alias, "router_model": model,
                     "best_model": best_model, "regret": max(0.0, regret),
                     "verdict": "match" if regret <= 0 else "mismatch"})
    return rows


def recommend_config(star_rows: List[Dict[str, Any]]) -> Tuple[str, List[str]]:
    """A model_catalog using only keys K9ModelRouter reads (provider,
    llm_ref, capabilities, default_model). Each capability on exactly one
    entry, because K9ModelRouter gives ties to the first entry."""
    best = best_by_type(star_rows)
    if not best:
        return "", []
    by_model: Dict[str, List[str]] = defaultdict(list)
    for ttype in TASK_TYPES:
        if ttype in best:
            by_model[best[ttype]["model"]].append(ttype)
    default = max(by_model, key=lambda m: (("chat" in by_model[m]), len(by_model[m])))
    aliases: Dict[str, str] = {}
    for model in by_model:
        if model == default:
            aliases[model] = "general"
        elif "reasoning" in by_model[model] and "reasoning" not in aliases.values():
            aliases[model] = "reasoning"
        else:
            aliases[model] = safe_name(model)
    llm_models = {aliases[m]: {"model": m} for m in by_model}
    catalog = {}
    for model, types in by_model.items():
        caps: List[str] = []
        for t in types:
            for c in CAPABILITIES[t]:
                if c not in caps:
                    caps.append(c)
        if aliases[model] == "general" and "general" not in caps:
            caps.insert(0, "general")
        catalog[aliases[model]] = {"provider": "ollama", "llm_ref": aliases[model], "capabilities": caps}
    doc = {"inference": {"llm_factory": {"models": llm_models},
                         "model_catalog": {"default_model": "general", "models": catalog}}}
    notes = [f"{t}: {best[t]['model']} (quality {best[t]['quality']}"
             + (f"; tied on quality, higher overall score {best[t].get('score')})" if best[t].get("tie_broken") else ")")
             for t in TASK_TYPES if t in best]
    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=None), notes


def overall_winner(board: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return board[0] if board else None
