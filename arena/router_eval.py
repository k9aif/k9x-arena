# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
"""Router test: can K9ModelRouter, taught by this match's scores, pick the
best model for a task it has not seen?

Pass 1 already measured every contender on every task, so no model is run
again. Each task is held out in turn (leave-one-out): a fresh K9ModelRouter
learns from the other tasks' scores through its own record_feedback(), then
routes the held-out prompt. Its pick is scored with the pick's real pass-1
score, next to four baselines:

  best possible   the top contender on that task (upper bound)
  best single     the model with the best average on the *other* tasks, used for everything
  your rules      your router config (ARENA_ROUTER_*), rules only, by task type
  random          the average contender

This is how evaluation-trained routers (Not Diamond, RouteLLM, RouterBench)
are measured: quality recovered versus the best single model and the best
possible pick. The router is the framework's own class; the arena only
feeds it evidence and asks route(), which makes no model call."""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

from k9_aif_abb.k9_inference.catalog.model_catalog import ModelCatalog
from k9_aif_abb.k9_inference.models.inference_request import InferenceRequest
from k9_aif_abb.k9_inference.routers.k9_model_router import K9ModelRouter
from k9_aif_abb.k9_storage.routing_state_store import RoutingStateStore
from k9_aif_abb.k9_storage.sqlite_database_storage import SQLiteDatabaseStorage

from arena.settings import alias_for

TIE = 0.5   # scores this close count as "picked the best"


def task_quality(tasks: List[Dict[str, Any]], runs: List[Dict[str, Any]],
                 scores: Dict[int, Dict[str, Any]]) -> Dict[str, Dict[str, Dict[str, float]]]:
    """task_id -> model -> {q: mean final score, ms: mean latency} over pass-1 runs."""
    active = {t["id"] for t in tasks if not (t.get("screen") or {}).get("excluded")}
    acc: Dict[str, Dict[str, Dict[str, List[float]]]] = defaultdict(lambda: defaultdict(lambda: {"q": [], "ms": []}))
    for r in runs:
        if r["mode"] != "forced" or r["task_id"] not in active or r["id"] not in scores:
            continue
        cell = acc[r["task_id"]][r["model"]]
        cell["q"].append(float(scores[r["id"]]["score"]))
        if r.get("latency_ms"):
            cell["ms"].append(float(r["latency_ms"]))
    return {tid: {m: {"q": round(statistics.mean(v["q"]), 2),
                      "ms": round(statistics.mean(v["ms"]), 1) if v["ms"] else None}
                  for m, v in by_model.items()}
            for tid, by_model in acc.items()}


def _router(models: Dict[str, Dict[str, Any]], default: str, learning: Dict[str, Any]) -> K9ModelRouter:
    cfg = {"inference": {"router": {"learning": {"refresh_seconds": 10 ** 6, **learning}}}}
    store = RoutingStateStore(SQLiteDatabaseStorage(db_path=":memory:"))
    return K9ModelRouter(catalog=ModelCatalog({"default_model": default, "models": models}),
                         config=cfg, state_store=store)


def rules_picks(task_types: List[str], rules: Dict[str, Dict[str, Any]]) -> Dict[str, Optional[str]]:
    """task type -> model your router config sends it to (rules only)."""
    entries = {a: e for a, e in rules.items() if e.get("model")}
    if not entries:
        return {}
    models = {a: {"provider": "ollama", "llm_ref": a, "capabilities": list(e.get("capabilities", []))}
              for a, e in entries.items()}
    default = "general" if "general" in models else next(iter(models))
    router = _router(models, default, {"enabled": False})
    return {t: entries[router.route(InferenceRequest(prompt=".", task_type=t)).model_alias]["model"]
            for t in task_types}


def evaluate(tasks: List[Dict[str, Any]], quality: Dict[str, Dict[str, Dict[str, float]]],
             contenders: List[str], rules: Dict[str, Dict[str, Any]],
             learning: Optional[Dict[str, Any]] = None, progress=None) -> Dict[str, Any]:
    learning = dict(learning or {})
    by_id = {t["id"]: t for t in tasks}
    evald = [tid for tid in by_id if len(quality.get(tid, {})) >= 2]
    if len(evald) < 3 or len(contenders) < 2:
        return {"available": False,
                "reason": "The router test needs at least two contenders and three scored tasks."}

    alias = {m: alias_for(i) for i, m in enumerate(contenders)}
    model_of = {a: m for m, a in alias.items()}
    rule_by_type = rules_picks(sorted({by_id[t]["type"] for t in evald}), rules)
    # Cold-start prior mirrors your config where its models are competing.
    caps: Dict[str, List[str]] = defaultdict(list)
    for e in rules.values():
        if e.get("model") in alias:
            caps[e["model"]] += [c for c in e.get("capabilities", []) if c not in caps[e["model"]]]
    general = (rules.get("general") or {}).get("model")
    default = alias[general] if general in alias else alias[contenders[0]]
    catalog = {alias[m]: {"provider": "ollama", "llm_ref": alias[m], "capabilities": caps.get(m, [])}
               for m in contenders}

    rows: List[Dict[str, Any]] = []
    for i, tid in enumerate(evald):
        if progress:
            progress(i, len(evald))
        task = by_id[tid]
        router = _router(catalog, default, learning)
        for other in evald:
            if other == tid:
                continue
            for model, v in quality[other].items():
                if model in alias:
                    router.record_feedback(by_id[other]["prompt"], alias[model], v["q"],
                                           task_type=by_id[other]["type"], latency_ms=v["ms"], source="arena")
        d = router.route(InferenceRequest(prompt=task["prompt"], task_type=task["type"]))
        q = {m: v["q"] for m, v in quality[tid].items()}
        best = max(q.values())
        learned_model = model_of.get(d.model_alias, d.model_alias)
        others = [o for o in evald if o != tid]
        single = max(q, key=lambda m: (statistics.mean([quality[o][m]["q"] for o in others if m in quality[o]] or [0]), m))
        rule_model = rule_by_type.get(task["type"])
        rows.append({
            "task_id": tid, "type": task["type"], "title": task.get("title", ""),
            "scores": q, "best": best, "best_models": sorted(m for m, v in q.items() if best - v <= TIE),
            "learned_model": learned_model, "learned_q": q.get(learned_model),
            "strategy": d.strategy, "predicted": d.predicted_quality, "rationale": d.rationale,
            "had_evidence": d.predictions is not None,
            "single_model": single, "single_q": q.get(single),
            "rules_model": rule_model, "rules_q": q.get(rule_model) if rule_model in q else None,
            "random_q": round(statistics.mean(q.values()), 2),
        })
    if progress:
        progress(len(evald), len(evald))

    def summary(key: str) -> Optional[Dict[str, Any]]:
        vals = [(r[key], r["best"]) for r in rows if r[key] is not None]
        if not vals:
            return None
        return {"avg": round(statistics.mean(v for v, _ in vals), 1),
                "best_picks": sum(1 for v, b in vals if b - v <= TIE), "tasks": len(vals)}

    oracle = round(statistics.mean(r["best"] for r in rows), 1)
    strategies = {
        "oracle": {"avg": oracle, "best_picks": len(rows), "tasks": len(rows)},
        "learned": summary("learned_q"),
        "single": summary("single_q"),
        "rules": summary("rules_q"),
        "random": summary("random_q"),
    }
    single_all = Counter(r["single_model"] for r in rows).most_common(1)[0][0]
    L, S = strategies["learned"], strategies["single"]
    headroom = oracle - S["avg"]
    by_type: Dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        by_type[r["type"]][r["learned_model"]] += 1
    return {
        "available": True,
        "tasks": len(rows),
        "strategies": strategies,
        "single_model": single_all,
        "vs_single": round(L["avg"] - S["avg"], 1),
        "headroom_captured": round(100 * (L["avg"] - S["avg"]) / headroom) if headroom > 0.05 else None,
        "learned_decisions": sum(1 for r in rows if r["strategy"] == "learned"),
        "without_evidence": sum(1 for r in rows if not r["had_evidence"]),
        "picks_by_type": {t: dict(c) for t, c in by_type.items()},
        "rows": rows,
    }
