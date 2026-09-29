# K9X Arena — Project Plan

**Status (2026-09-28): phases 0–4 built and verified end to end** against a real
Ollama host (Quick Check suite; screening, forced + router runs, all graders,
Guardian safety pass, judge, stars, router audit, recommended config, UI).
See §8 for what's next.

Companion to [SPEC.md](SPEC.md) (what the arena does). This file covers how
it is configured, what it looks like, how it is built, and in what order.

## 1. Goal

A local-first K9-AIF application that puts LLMs head to head on a task
suite, grades and stars every answer, audits the Intelligent Model Router,
and recommends a `model_catalog` for real solutions. It should feel like an
arena (models as contenders, a live match, a scoreboard, a history of past
matches), not like an admin dashboard.

## 2. Configuration: `.env` vs `config.yaml`

Rule: **`.env` says *where* and *which* (hosts, model tags, secrets);
`config.yaml` says *how* (behaviour, weights, thresholds).** `config.yaml`
reads `.env` values through the framework's `load_yaml`, which expands
`${VAR:-default}` anywhere in the file.

### `.env` (gitignored; `.env.example` committed)

```bash
# Ollama host the contestants and judge run on
OLLAMA_BASE_URL=http://localhost:11434

# Contestants: comma-separated Ollama tags. Each becomes a catalog entry
# contestant_1..n at start-up, so no model name is hardcoded anywhere.
ARENA_CONTESTANTS=qwen3.8:27b,gemma4:31b,qwen2.5:32b

# Judge: must not be one of the contestants (checked at start-up)
ARENA_JUDGE_MODEL=deepseek-r1:32b

# Granite Guardian: mandatory in the arena (no on/off switch, fails closed)
ARENA_GUARDIAN_MODEL=granite4.1-guardian:8b

# Web UI
ARENA_PORT=8110
ARENA_USER=demo
ARENA_PASSWORD=demo
ARENA_ADMIN_USER=admin
ARENA_ADMIN_PASSWORD=

# Storage
ARENA_DB_PATH=./runtime/arena.db
```

### `config.yaml` (committed)

```yaml
arena:
  contestants: "${ARENA_CONTESTANTS:-qwen2.5:7b}"
  judge_model: "${ARENA_JUDGE_MODEL:-qwen2.5:7b}"
  runs_per_task: 3
  router_mode: true                 # also run every task through K9ModelRouter
  task_suites_dir: ./suites         # built-in suites (YAML)
  sandbox:
    code_timeout_seconds: 20        # generated code runs in a subprocess only
  scoring:
    weights: { quality: 0.60, consistency: 0.15, latency: 0.15, refusal_accuracy: 0.10 }
    stars: { 5: 90, 4: 75, 3: 60, 2: 40 }   # score thresholds; below 40 = 1 star
  judging:
    passes: 2                       # second pass shuffles answer order
    hil_on_disagreement_grades: 1   # >1 grade apart -> RequiresHIL -> K9X HIL

governance:
  guardian:
    enabled: true                   # fixed: the arena ignores any attempt to turn it off
    model: "${ARENA_GUARDIAN_MODEL:-granite4.1-guardian:8b}"
    on_unavailable: fail_closed     # fixed

security:
  shield:
    enabled: true                   # all 13 k9x_Shield checks, ingress + egress

inference:
  router:
    type: k9_model_router
    default_model: general
    persistence: { enabled: true, provider: sqlite }
  # model_catalog is built at start-up from arena.contestants and
  # arena.judge_model: contestant_<n> (capability contestant_<n>), judge
  # (capability judge), plus the production-style entries under test
  # (general / reasoning) for router mode.
```

**Guardian is mandatory.** Unlike K9X Studio, the arena has no
`GUARDIAN_ENABLED` switch. If the Guardian model is unreachable, uploads are
refused and the header shows *Guardian Offline*; the arena never scans
"best effort".

## 3. The arena experience (UI)

Design mockups (Lobby, Live match, Results): K9X Arena Design canvas. The live match's second view became the K9X Octagon (below).

Same visual family as the K9X Studio landing page (dark navy, teal and
amber, subtle background artwork), but staged like a sports broadcast:
contenders, a live match, a scoreboard.

### 3.1 Lobby (home)
- **Header status bar:** Ollama host reachable · Guardian Live/Offline ·
  model currently loaded on the GPU (from `ollama ps`).
- **Contender roster:** one card per Ollama model (from `/api/tags`):
  name, family, size, quantization, pulled date, win record and star
  average from past matches. Toggle which ones enter the next match.
- **Last match** podium (gold/silver/bronze) and a **Start a match** button.

### 3.2 New match
- Pick 2–4 contenders and a judge (the judge list excludes the contenders).
- Pick a task suite: a built-in suite, or **upload** one.
- Runs per task, router mode on/off, estimated run time (models × runs ×
  tasks, from past latency).

### 3.3 Upload (Guardian-gated)
- Accepts a task suite (`.yaml`) or a source document (`.md`, `.txt`) that
  the arena turns into extraction and summarization tasks.
- Every upload shows the same three-step strip used in Studio:
  **Pre-check → Granite Guardian scan → Accepted / Blocked**, with the
  Guardian verdict and reason recorded against the upload.
- Nothing reaches a contender until the scan passes.

### 3.4 Live match
- One lane per contender; task cards move through the lanes as each model
  answers. Each card shows latency, a streaming preview of the output, and
  its grade the moment it is graded (stars fill in).
- A running scoreboard at the top; a ticker of trace events (LLM calls,
  Shield and Guardian verdicts) along the bottom.
- A "GPU swap" marker whenever Ollama unloads one model for another, so
  swap cost is visible, not hidden in latency.
- Items sent to human review show a *Pending HIL* badge until decided.
- **The K9X Octagon** (toggle: Lanes | Octagon): a top-down cage whose border
  is the Granite Guardian ring; the Intelligent Model Router is the K9X mat at
  center stage; contenders hold fixed corners (the one answering is
  spotlighted, with teal request and amber answer dashes to the center); the
  judges' table sits cageside; the crowd fills the stands with camera flashes.
  Still under `prefers-reduced-motion`.

### 3.5 Results
- **Star grid:** contenders × task types, 1–5 stars, with the score on hover.
- **Leaderboard:** score, stars, p50/p95 latency, refusal rate, consistency.
- **Router audit:** for each task type, the router's pick vs the best
  contender, and the regret in quality points.
- **Recommended config:** the `model_catalog` YAML with a copy button.
- Judge disagreement rate shown next to every judged score.

### 3.6 Drill-down
- One task: prompt, every contender's output side by side, deterministic
  grader result, both judge passes with rationale, HIL decision if any.

### 3.7 History
- Every match: date, contenders, suite, winner, duration.
- A model's trend across matches (score and stars over time).
- Re-run a past match with new contenders; compare two matches side by side.

## 4. Architecture

- **K9-AIF solution** (SBBs on `k9_aif_abb`): `ArenaRouter` →
  `ArenaOrchestrator` → Suite / Contestant / Grading / Report squads, as in
  SPEC.md. Every LLM call goes through `llm_invoke`.
- **Backend:** FastAPI (same stack as K9X Studio), SQLite store, Server-Sent
  Events for the live match, fed by the framework's trace-events bus.
- **Frontend:** React + Vite + TypeScript, built into the package.
- **Upload scanning:** Granite Guardian through the framework's
  `GuardianGovernance`, fail-closed.
- **Human review:** judge disagreements raise `RequiresHIL`; decisions come
  back through K9X HIL and resume the match (`arena.resume`).

### Data model (SQLite)

| Table | Holds |
|---|---|
| `matches` | id, started/finished, suite, settings snapshot, status |
| `contenders` | match id, model tag, catalog alias |
| `tasks` | suite task id, type, prompt, grader inputs |
| `runs` | match, contender, task, run #, output, latency, tokens, refused?, error |
| `grades` | run id, grader (deterministic / judge 1 / judge 2 / HIL), score, grade, rationale |
| `stars` | match, contender, task type, score, stars |
| `router_audit` | legacy (matches before the router test); the router test is stored in the report |
| `uploads` | file name, hash, Guardian verdict and reason, accepted? |

## 5. Phases

| Phase | Delivers | Done when |
|---|---|---|
| **0. Scaffold** | Feed SPEC.md to K9X Studio; `.env.example`, `config.yaml`, catalog built from `.env` | App starts; `/api/health` and Guardian status work |
| **1. Engine** | Forced runs, deterministic graders (code, extraction, reasoning), SQLite store, CLI run | A 2-model × 1-run match of the built-in suite completes from the CLI |
| **2. Judging and stars** | Two-pass judge via `K9PromptEvaluator`, scoring, stars, HIL on disagreement | Star grid correct on a hand-checked sample |
| **3. Arena UI** | Lobby, new match, Guardian-gated upload, live match, results, drill-down | A full match runs end to end in the browser |
| **4. Router audit** | Router mode, regret report, recommended `model_catalog` | Recommendation pasted into K9Chat's config produces the expected routing |
| **5. History** | Match history, model trends, re-run, compare | Two matches comparable side by side |
| **6. Ship** | README, tests, container, `pip` package | Clean install on the PowerAI box runs a match |

## 6. Risks

- **GPU time:** one GPU, one model loaded at a time; long runs must be
  resumable and ordered by model to minimise swaps.
- **Judge bias:** mitigated by anonymisation, two passes and HIL, never
  eliminated; the UI says so.
- **Guardrail refusals:** stricter models may refuse legitimate domain
  tasks; the arena counts refusals separately from wrong answers.
- **Running generated code:** subprocess sandbox with timeout only; no
  network, no writes outside a temp dir.

## 7. Open decisions

1. **Uploads:** task suites and `.md`/`.txt` source documents only, or also
   PDF (needs Docling for text extraction)?
2. **Hosting:** local only, or also a public demo at `arena.k9x.ai`? Public
   runs would queue on the PowerAI GPU.
3. **Packaging:** its own PyPI package (`k9x-arena`), or a subcommand of
   `k9x` (`k9x arena`)?

## 8. Built vs. next

**Built (v0.1):** everything in phases 0–4, plus history, the review queue,
uploads with mandatory Guardian scanning, the Lanes and Octagon live views, and
an Architecture tab.

**Next:**
1. **K9X HIL for disputed grades.** Today disagreements go to an in-app review
   queue; route them through `RequiresHIL` → K9X HIL when Kafka is available.
2. ~~A learning router (framework)~~: done in k9-aif 1.13.0 (2026-09-29).
   K9ModelRouter predicts per-prompt quality from graded evidence
   (`record_feedback`, similarity-weighted k-NN, Not Diamond / RouteLLM
   style). The arena's second, routed pass became the held-out router test
   (`router_eval.py`). Next: an "Export evidence" button that writes a match's
   grades into a production router's `routing_outcomes`.
3. ~~Live grades during answering~~ — done: code, extraction and reasoning
   are graded as each answer lands.
4. **Packaging:** `pip install k9x-arena` or a `k9x arena` subcommand; container
   for the PowerAI host.
