# K9X Arena

Put LLMs head to head on a task suite. K9X Arena grades every answer, awards
1–5 stars per model per task type, audits the K9-AIF **Intelligent Model
Router** (did it send each task to the model that actually did best?), and
writes the `model_catalog` your router should use.

It runs on your own machine against your own Ollama models. Your prompts and
documents never leave it.

![K9X Arena architecture](web/architecture.png)

## What makes it different

- **Router audit.** Every task also goes through K9-AIF's K9ModelRouter with
  its real task type. The report shows where the router picked a worse model
  and how many quality points that cost ("router regret").
- **Config you can paste.** Results end with a recommended `model_catalog`
  that uses only keys K9ModelRouter reads.
- **Governed by default.** Granite Guardian screens every task prompt and
  every upload, and fails closed: if Guardian is down, nothing runs.
  k9x_Shield checks prompts too.
- **Graded against right answers first.** Code runs its unit tests in a
  sandbox, extraction is checked field by field, reasoning against the
  expected answer. An LLM judge is used only for open-ended answers, twice,
  anonymized; disagreements go to a review queue.

## Quick start

You need Python 3.11+, an Ollama host, and the models you want to compare.

```bash
git clone https://github.com/k9aif/k9x-arena.git
cd k9x-arena
cp .env.example .env                 # set OLLAMA_BASE_URL and your model tags
ollama pull granite4.1-guardian:8b   # Guardian is mandatory
./run.sh                             # installs requirements (k9-aif from PyPI) and starts
```

`./run.sh` first runs a pre-flight check and stops with a clear message if
`.env` is missing, the Ollama host is unreachable, Granite Guardian isn't
pulled or doesn't answer, the judge isn't pulled, or no contender is pulled
(run it alone with `python -m arena.preflight`).

Open `http://localhost:8111` and sign in (`demo` / `demo` by default; set
`ARENA_ADMIN_PASSWORD` in `.env` to enable the admin login). Start with the
**Quick Check** suite (6 tasks) to see a full match in minutes, then run
**Claims Ops Starter** (30 tasks).

## Configuration

`.env` says *where* and *which*; `config.yaml` says *how*.

| `.env` | Meaning |
|---|---|
| `OLLAMA_BASE_URL` | Ollama host serving contenders, judge and Guardian |
| `ARENA_CONTESTANTS` | Contenders offered by default (any pulled model can be picked per match) |
| `ARENA_JUDGE_MODEL` | Judge for open-ended answers; never also a contender |
| `ARENA_ROUTER_GENERAL_MODEL`, `ARENA_ROUTER_REASONING_MODEL` | The two catalog entries the router audit tests |
| `ARENA_GUARDIAN_MODEL` | Granite Guardian model (mandatory) |
| `ARENA_USER` / `ARENA_PASSWORD`, `ARENA_ADMIN_USER` / `ARENA_ADMIN_PASSWORD` | Logins |

`config.yaml` holds runs per task, scoring weights, star thresholds, judge
passes, the review rule, the code sandbox timeout and the router-under-test
capabilities.

## How a match runs

1. **SuiteSquad**: load the suite; screen every prompt with k9x_Shield and Granite Guardian.
2. **ContestantSquad**: run every task on every contender (grouped by model so the GPU swaps rarely), then once through the router.
3. **GradingSquad**: deterministic graders, Guardian's safety pass on adversarial answers, then two judge passes.
4. **ReportSquad**: scores and stars, router audit, recommended config.

Every model call goes through the framework's `llm_invoke` and K9ModelRouter.
To pin a task to one contender, each contender gets its own catalog entry with
a unique capability, so even forced runs are routed by the router itself.

## Container (Ubuntu / Podman)

Like the other K9X components, `ubuntu/` builds and runs a single container on
port **8111**, reading your `.env` at start. Match history lives in
`~/containers/volumes/k9x-arena/runtime` on the host, so it survives rebuilds.

```bash
./ubuntu/build-run.sh all     # build + start
./ubuntu/build-run.sh logs    # pre-flight results appear first
```

If the pre-flight fails, the container stops and the log says why.

## Your own tasks

Upload a task suite (`.yaml`, same format as `suites/`) or a source document
(`.md`, `.txt`) that becomes summary and question tasks. Every upload is
pre-checked, then Granite Guardian scans each task prompt once, at upload;
matches reuse those verdicts. A normal task that Guardian flags gets the upload
rejected (adversarial tasks are expected to be flagged).

Built-in suites in `suites/` ship with the arena and are trusted: they are not
Guardian-scanned at match time (k9x_Shield still checks every contender call).
If you edit them locally, that is your own review.

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests -q
```

## Status and limits

- One match at a time (one GPU). Matches can be paused and resumed.
- Judge disagreements go to an in-app review queue; routing them to K9X HIL
  is planned.
- K9ModelRouter scores each request on its own and doesn't yet learn from
  past results; the arena's recommended config is how evidence feeds back
  into it today.

See [SPEC.md](SPEC.md) and [project_plan.md](project_plan.md).

Part of the [K9X ecosystem](https://github.com/k9aif/k9x-ecosystem), built on
the [K9-AIF framework](https://github.com/k9aif/k9-aif-framework). Apache 2.0.
