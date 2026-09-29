# K9X Arena — Process Specification

## Purpose

K9X Arena runs the same task suite on several LLMs, grades and rates every
answer, awards 1–5 stars per model per task type, and tests the K9-AIF
Intelligent Model Router: taught by the match's scores, does the router pick
for a held-out task the model that
actually did best? Its final output is a recommended
`inference.model_catalog` block the team can paste into a solution's
`config/config.yaml`.

It is a K9-AIF solution (SBBs on `k9_aif_abb`), not a standalone benchmark
script. Every LLM call goes through `llm_invoke` and the router.

## Users

- **Solution architect:** decides which model serves which capability in a
  K9-AIF solution, and wants evidence rather than vendor claims.
- **Reviewer (via K9X HIL):** settles grades the automated judges disagree on.

## Inputs

1. **Contestant list:** Ollama model tags, e.g. `qwen3.8:27b`, `gemma4:31b`,
   `qwen2.5:32b`. Set in `.env`; no model is hardcoded.
2. **Judge model:** a model that is not a contestant, e.g. `deepseek-r1:32b`.
3. **Task suite:** a YAML file of tasks. Each task has an id, a task type, a
   prompt, and whatever its grader needs (unit tests, expected JSON,
   expected answer, or a rubric). Start with about 30 tasks, 5 per type.
4. **Run settings:** runs per task (default 3), and whether to run router
   mode.

## Task types and how each is graded

| Task type | Example | Grader | Needs a judge LLM? |
|---|---|---|---|
| code | "Write a function that parses ISO dates" | Run hidden unit tests in a sandboxed subprocess; score = tests passed | No |
| extraction | Pull fields from an invoice or claim text | Compare to expected JSON field by field | No |
| reasoning | Logic or arithmetic word problem | Exact match on the expected final answer | No |
| summarization | Summarize a policy document | LLM judge against a reference summary | Yes |
| chat | Customer-support reply | LLM judge against a rubric | Yes |
| adversarial | Prompt injection, PII bait, jailbreak | k9x_Shield + Granite Guardian verdicts, plus whether the model refused when it should and complied when it should | No |

Deterministic graders always win over the judge when both apply.

## Process

1. **Load suite.** Validate the task suite and contestant list. Reject tasks
   whose grader inputs are missing.
2. **Screen inputs.** Every task prompt passes k9x_Shield ingress (the
   adversarial tasks are expected to be flagged; that is recorded, not
   blocked, for those tasks only).
3. **Run contestants (forced mode).** For each model, run every task N times.
   Group all calls by model so the GPU swaps models as rarely as possible.
   Record output, latency, output tokens, refusals, and errors.
4. *(Removed: a second, routed generation pass. It only re-read the
   router's fixed rules. The router test in step 8 replaces it.)*
5. **Grade.** Deterministic graders first. Open-ended outputs go to two
   judges: `K9PromptEvaluator` (A–F, five weighted dimensions) using the
   judge model, and a second judge pass with the model order shuffled.
   Outputs are anonymized — a judge never sees which model wrote an answer,
   and no model judges its own output.
6. **Escalate disputes.** If the two judge passes disagree by more than one
   grade, raise `RequiresHIL` so a human reviewer decides in K9X HIL. The
   run continues; the item is marked pending until the decision arrives.
7. **Score and award stars.** Per model per task type:
   quality 60%, consistency across runs 15%, latency 15%, refusal accuracy
   10%. Map the 0–100 score to 1–5 stars (≥90 ★★★★★, ≥75 ★★★★, ≥60 ★★★,
   ≥40 ★★, else ★).
8. **Test the router (held out).** For each task, a fresh `K9ModelRouter`
   learns the other tasks' pass-1 scores through `record_feedback()` and
   routes the held-out prompt with `route()` (no model call). Score its pick
   with that model's real result, next to the best possible pick, the best
   single model (chosen on the other tasks), your config's rules and a random
   pick. This is RouterBench-style evaluation.
9. **Recommend config.** Produce a suggested `inference.model_catalog` and
   `llm_factory.models` block that maps each capability to its best model,
   with each capability on exactly one entry (ties in K9ModelRouter always
   go to the first listed model).
10. **Report.** Leaderboard, star grid, router test, per-task drill-down,
    and the recommended config, in a web UI with live progress streamed from
    the framework's trace-events bus.

## How models are pinned without bypassing the router

Agents never call `LLMFactory` or the router directly. To force a task onto
one model, the solution's model catalog gets one entry per contestant with a
unique capability (`contestant_<n>`), plus a `judge` entry with capability
`judge`. Forced mode sends `task_type="contestant_<n>"`; the judge sends
`task_type="judge"`; router mode sends the real task type (`code`,
`reasoning`, …) against the production-style entries.

## Components (K9-AIF)

- **Router:** `ArenaRouter` (K9EventRouter) — event types `arena.run`,
  `arena.resume` (HIL decisions).
- **Orchestrator:** `ArenaOrchestrator` — runs the squads in order, catches
  `RequiresHIL`, applies Zero Trust at its boundary.
- **Squads and agents:**
  - `SuiteSquad`: `SuiteLoaderAgent` (BaseAgent), `InputScreenAgent`
    (BaseAgent, Shield ingress).
  - `ContestantSquad`: `ForcedRunAgent` (BaseAgent)
    (BaseAgent).
  - `GradingSquad`: `CodeGraderAgent`, `ExtractionGraderAgent`,
    `ReasoningGraderAgent` (BaseAgent, deterministic), `JudgeAgent`
    (K9ValidationLoopAgent wrapping K9PromptEvaluator), `SafetyGraderAgent`
    (BaseAgent, Shield + Guardian).
  - `ReportSquad`: `ScoringAgent` (BaseAgent), `RouterTestAgent`
    (BaseAgent), `ConfigRecommenderAgent` (BaseAgent).
- **Governance:** k9x_Shield and Granite Guardian on every agent (standard).
- **Storage:** SQLite by default (runs, outputs, grades, stars); the
  router's `routing_decisions` table is read, not duplicated.

## Outputs

- Star grid: models × task types.
- Leaderboard with score, stars, p50/p95 latency, refusal rate, consistency.
- Router test: learned router vs best possible, best single model, your rules, random; every pick with its rationale.
- Recommended `model_catalog` YAML.
- Per-task drill-down: prompt, each model's output, grades, judge rationale.
- Exportable run report (Markdown and CSV).

## Constraints

- Local-first: Ollama on one GPU host; endpoint and model tags from `.env`
  (`OLLAMA_BASE_URL`, contestant and judge tags). No hardcoded IPs or models.
- Sequential by model on a single GPU; a full 3-model × 3-run × 30-task run
  plus judging takes hours, so runs are resumable.
- Generated code runs only in a sandboxed subprocess with a timeout, never
  in the arena's own process.
- LLM judges are biased; the report shows judge disagreement rates next to
  every judged score and never presents a judged score as ground truth.

## Out of scope (first version)

- Hosted-API contestants (OpenAI, Anthropic, watsonx) — the design allows
  them; the first version targets Ollama.
- Image or audio tasks (the framework's InferenceRequest is text-only).
- Automatically changing a live router's config — the arena recommends; a
  human applies.
