# CLAUDE.md — K9X Arena

Read `k9-aif-framework/CLAUDE.md` and `k9x-ecosystem/CLAUDE.md` first; this
file covers only what's specific to the arena.

## What it is

A K9-AIF solution (SBBs on `k9_aif_abb`, installed from PyPI) that runs LLMs
head to head, grades them, stars them, audits K9ModelRouter and recommends a
`model_catalog`. FastAPI backend + vanilla-JS UI in `web/` (no build step).

## Layout

```
arena/
  api.py            FastAPI routes, SSE stream, uploads (Guardian scan), static UI
  engine.py         one match at a time, in a background thread → ArenaRouter
  router/           ArenaRouter (BaseRouter): arena.run, arena.rescore
  orchestrators/    ArenaOrchestrator (BaseOrchestrator): builds the per-match
                    catalog, resets LLMFactory/ModelRouterFactory, runs squads
  squads/           arena_squads.yaml (Suite, Contestant, Grading, Report)
  agents/           the 12 agents + yaml/ role/goal files
  graders.py        deterministic graders (code sandbox, extraction, reasoning, adversarial)
  scoring.py        stars, leaderboard, router audit, recommended config (pure functions)
  store.py          SQLite
  live.py           running-match state, pause/cancel, trace_events → event log
  settings.py       .env + config.yaml; build_inference_config() per match
suites/             built-in task suites (YAML)
web/                index.html, app.js, styles.css, architecture.png
docs/architecture.puml
```

## Rules to keep

- **Every model call goes through `llm_invoke`.** Forced mode pins a
  contender via its unique capability (`contestant_<n>`); the judge uses
  `judge`; router mode sends the real task type. Never call LLMFactory or the
  router directly.
- **Granite Guardian is mandatory and fails closed** — `settings.load_config()`
  forces it on; uploads and match starts are refused when it's offline.
- **Batch GPU work by phase** (screen → answer by model → router → grade →
  judge) so Ollama swaps models as rarely as possible. Don't interleave
  Guardian or judge calls with contender calls.
- **Escape all model output** in the UI (`esc()` in app.js).
- The recommended config must use only keys K9ModelRouter reads
  (`provider`, `llm_ref`, `capabilities`, `default_model`), each capability on
  one entry — there's a test for this.
- Regenerate the diagram with
  `PLANTUML_LIMIT_SIZE=8192 plantuml -tpng -Sdpi=160 docs/architecture.puml -o ../web`
  then rename `web/k9x_arena_architecture.png` to `web/architecture.png`.
