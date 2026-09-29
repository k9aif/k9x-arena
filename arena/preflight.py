# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
"""Pre-flight check run by ./run.sh before the server starts.

Fails fast (exit 1) when the arena could not run a match: no .env, Ollama
host unreachable, Granite Guardian missing or not answering (mandatory),
judge missing, or no contender pulled. Missing optional models are warnings.

    python -m arena.preflight
"""

from __future__ import annotations

import os
import sys
from typing import List, Tuple

import requests

from arena.settings import ROOT, default_contestants, judge_model, load_config, ollama_base_url, router_under_test

OK, WARN, FAIL = "ok", "warn", "fail"


def check(tags_timeout: float = 5.0, guardian_timeout: float = 90.0) -> List[Tuple[str, str]]:
    """Returns [(level, message)]. No side effects."""
    results: List[Tuple[str, str]] = []
    # A container gets its settings via --env-file, so the environment itself counts.
    if not (ROOT / ".env").exists() and not os.environ.get("OLLAMA_BASE_URL"):
        return [(FAIL, "No .env file. Copy .env.example to .env and set OLLAMA_BASE_URL and your models.")]

    url = ollama_base_url()
    if not os.environ.get("OLLAMA_BASE_URL"):
        results.append((WARN, f"OLLAMA_BASE_URL not set in .env; using {url}"))
    try:
        resp = requests.get(f"{url}/api/tags", timeout=tags_timeout)
        resp.raise_for_status()
        pulled = {m.get("name") for m in resp.json().get("models", [])}
    except Exception as exc:
        return results + [(FAIL, f"Ollama host not reachable at {url} ({exc.__class__.__name__}). "
                                 "Start Ollama or fix OLLAMA_BASE_URL in .env.")]
    results.append((OK, f"Ollama reachable at {url} · {len(pulled)} models pulled"))

    cfg = load_config()
    guardian = cfg["governance"]["guardian"]["model"]
    if guardian not in pulled:
        results.append((FAIL, f"Granite Guardian model '{guardian}' is not pulled (mandatory). "
                              f"Run: ollama pull {guardian}"))
    else:
        try:
            r = requests.post(f"{url}/api/generate", timeout=guardian_timeout, json={
                "model": guardian, "prompt": "hi", "stream": False, "options": {"num_predict": 4}})
            r.raise_for_status()
            results.append((OK, f"Granite Guardian answering ({guardian})"))
        except Exception as exc:
            results.append((FAIL, f"Granite Guardian '{guardian}' is pulled but did not answer "
                                  f"({exc.__class__.__name__}). The arena never runs unscreened."))

    judge = judge_model(cfg)
    contenders = default_contestants(cfg)
    if not judge:
        results.append((FAIL, "ARENA_JUDGE_MODEL is not set in .env."))
    elif judge not in pulled:
        results.append((FAIL, f"Judge model '{judge}' is not pulled. Run: ollama pull {judge}"))
    else:
        results.append((OK, f"Judge available ({judge})"))
    if judge and judge in contenders:
        results.append((WARN, f"'{judge}' is both the judge and a default contender; the Lobby will "
                              "keep it out of the contest."))

    available = [c for c in contenders if c in pulled]
    missing = [c for c in contenders if c not in pulled]
    if not contenders:
        results.append((FAIL, "ARENA_CONTESTANTS is empty in .env."))
    elif not available:
        results.append((FAIL, "None of ARENA_CONTESTANTS is pulled: " + ", ".join(missing)))
    else:
        results.append((OK, f"Contenders available: {', '.join(available)}"))
    if missing and available:
        results.append((WARN, f"Not pulled, so not offered: {', '.join(missing)}"))

    for alias, entry in router_under_test(cfg).items():
        tag = entry["model"]
        if not tag:
            results.append((WARN, f"ARENA_ROUTER_{alias.upper()}_MODEL not set; router mode will use the "
                                  "first contender for this entry."))
        elif tag not in pulled:
            results.append((WARN, f"Router-under-test '{alias}' model '{tag}' is not pulled; router mode "
                                  "will fail for tasks routed to it."))
    return results


def main() -> int:
    marks = {OK: "  ✓", WARN: "  !", FAIL: "  ✕"}
    print("K9X Arena pre-flight check")
    from arena.settings import showcase_mode
    if showcase_mode():
        print("  ✓ Public mode (ARENA_MODE=public): read-only, no Ollama or Guardian needed.")
        return 0
    results = check()
    for level, msg in results:
        print(f"{marks[level]} {msg}")
    failed = [m for lvl, m in results if lvl == FAIL]
    if failed:
        print(f"\nPre-flight failed ({len(failed)} problem{'s' if len(failed) > 1 else ''}). Fix .env or the "
              "Ollama host, then start the arena again.")
        return 1
    print("\nPre-flight passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
