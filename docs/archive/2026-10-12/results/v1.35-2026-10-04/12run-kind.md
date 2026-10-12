# AutoBench Service — 12 Parameterized Runs (KinD — single-node local, Helm install, agent runner direct (ETE-ext gateway))

**Report generated:** 2026-10-05T01:37:38Z  
**Service version:** `v1.35`  
**Platform:** KinD — single-node local, Helm install, agent runner direct (ETE-ext gateway)  
**Runs executed:** 12

All numbers below are derived programmatically from the mirrored S3 artifacts (`report.ndjson` / `token_report.ndjson` / `span_report.ndjson` / `manifest.json`) — none are transcribed.

**Contents**

- [S3 artifact locations](#s3-artifact-locations)
- [1. Terms](#1-terms)
- [2. Column names & meaning](#2-column-names--meaning)
- [3. The 12 run requests to the AutoBench Service](#3-the-12-run-requests-to-the-autobench-service)
- [4. Summary of the 12 run results](#4-summary-of-the-12-run-results)
- [5. Contents of the manifest file](#5-contents-of-the-manifest-file)
- [6. Per-run aggregate — input & output tokens](#6-per-run-aggregate--input--output-tokens)
- [7. Per-task detail](#7-per-task-detail)
- [8. Per-task span inventory](#8-per-task-span-inventory)
- [Reproducing this report](#reproducing-this-report)

## S3 artifact locations

Every number in this report is derived from artifacts published to S3. All objects for this report share one URL root and one key prefix:

| | |
|---|---|
| **URL root** | `https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/` |
| **Key prefix (all 96 objects)** | `kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/` |

The key layout is `<s3.prefix>/<caller>/<iss-host-encoded>/<benchmark>/<run_id>/<artifact>`, so **benchmark is a path segment** and selects cleanly (96 objects across 12 runs):

| benchmark | runs | objects | prefix (append to the key prefix above) |
|---|---:|---:|---|
| appworld | 2 | 16 | `appworld/` |
| gsm8k | 8 | 64 | `gsm8k/` |
| tau2 | 2 | 16 | `tau2/` |

**The model in use is NOT part of the key**, so it cannot be selected by prefix. Three of the models here happen to be prefix-separable only because tau2 and appworld each used a single model; gsm8k mixes models across its runs, so its prefix necessarily returns all of them. Models present: `openai/Azure/gpt-4.1`, `openai/Azure/gpt-5-mini-2025-08-07`, `openai/aws/claude-sonnet-5`, `openai/gemini-2.5-pro`. To fetch the objects for one model, resolve model -> `run_id` from the summary in section 4 and use the per-run prefixes.

Objects are readable **and listable anonymously** (no credentials needed), so treat anything written here as public. What is actually exposed: the **keys** carry the caller's username and the Keycloak issuer host. No artifact contains task prompts or model outputs — the record schema is entirely ids, counts and durations, and `span_report.*` is restricted to a fixed whitelist of structural and numeric span fields.

**Error text is classified, not published.** `run.json`'s `error` fields and `report.ndjson`'s `status_message` used to carry the upstream message verbatim, which put internal hostnames — including the LLM gateway's — a team UUID and a spend figure into this bucket across earlier runs. They now carry `<category> (shape <8 hex>)`, where the category comes from a closed set in `public_errors.py` and the shape is a hash of the message *template* (hostnames, URLs, ids and numbers removed before hashing, so a guessed hostname cannot be confirmed from it). Identical failures share a shape id, and the verbatim text remains available from the authenticated API and the Service log. **Runs published before that change still carry the raw strings** — the generators read both eras via `reference/causelib.py`.

## 1. Terms

| Term | Meaning |
|---|---|
| **Benchmark** | A named evaluation suite (`gsm8k`, `tau2`, `appworld`), each backed by an agent + an MCP tool service. |
| **Run** | One invocation of `run_benchmark` against a deployed benchmark (`POST /benchmarks/<b>/runs`). Identified by a `run_id`. A run selects the first `max_tasks` tasks and executes them, up to `max_parallel_sessions` at a time. |
| **Task** | One self-contained benchmark item. Each task runs in **complete isolation**: its own MCP session (`create_session`), its own prompt, one agent call, its own evaluation (`evaluate_session`), and its session is deleted afterward. |
| **Span** | The unit of OTEL instrumentation: one timed operation with a `name`, a parent, a status and attributes. A task's spans form a **tree** rooted at its `Agent.Session` span, and every number in §6/§7 is computed from them — `llm_count` counts `chat` spans, `tool_count` counts `execute_tool` spans, token totals come from `gen_ai.usage.*` on the `chat` spans. Exactly **four are named by the Service** (`Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `Evaluator.Evaluate`) and sit at the top two levels of the tree; **everything below `Agent.Call` is emitted inside the agent pod** — a few spans it names itself (`invoke_agent…`, `chat <model>`, `execute_tool <tool>`) and, dominating the raw count, its A2A/HTTP framework internals: a single gsm8k task emits ~100 spans, of which the Service names 4, a tau2 task ~400, an appworld task ~800. **Every span is now published per task in `span_report.ndjson`** and summarised in §8, so the aggregate counters can be audited rather than trusted. |
| **Independence** | Tasks within a run are **independent, not a pipeline** — no state flows between them. A task erroring or timing out fails only itself; the batch continues. |
| **Trace / Agent.Session** | Exactly one `Agent.Session` root span per task, keyed by `task_id`. Token usage is parsed from the `chat` (LLM) spans under that root — hence one `token_report` row per task. |
| **Session (`session_id`)** | The per-task MCP session handle from `create_session`. Distinct from `run_id` and `task_id`. |
| **median / mean** | Both are per-run centres over the run's per-task values. The **median** is the robust middle (half the tasks above, half below); the **mean** is the arithmetic average and is pulled by outliers. A mean well **above** the median signals **right-skew** — a few expensive tasks dominating — and that gap is itself informative, which is why §6 prints both instead of picking one. |
| **Coefficient of Variation (CV)** | **Population standard deviation ÷ mean** (σ/μ), computed over the run's per-task values. It is a *relative* spread measure and therefore **dimensionless**, which is the whole point: token counts differ by three orders of magnitude between benchmarks (~310 for gsm8k vs ~400,000 for appworld), so raw σ cannot be compared across them while CV can. Read it as "how uneven are the tasks within this run": **0** = every task identical, **~0.1** = tight and predictable, **~0.5** = markedly uneven, **≥1.0** = spread as large as the average itself (a couple of tasks dominate the bill). It is the *population* σ (divide by *n*, not *n−1*) because a run is the complete set of its tasks, not a sample drawn from a larger one, and it shares its denominator with **mean**, not median. Shown as `—` when undefined: fewer than 2 tasks, or a mean of 0. |
| **gsm8k** | "**Grade School Math 8K**" — ~8.5K grade-school arithmetic **word problems** (7,473 train + 1,319 test), loaded from HuggingFace by the MCP at startup (hence the `hf-secret` requirement). Each needs 2–8 elementary steps (+ − × ÷), no algebra or geometry, and has a single correct number graded by **exact match**; the difficulty is carrying a multi-step chain without slipping, not the arithmetic. `task_id` is an integer index. One task ≈ 1 real LLM call + 1 tool call, so it is the **cheapest leg and our infrastructure canary** — a gsm8k failure means deploy/auth/LLM-reachability/telemetry, not model capability. It **saturates near 1.0**, so never read it as a model comparison (use tau2 for that). |
| **appworld `<hash>_<n>` task ids** | appworld groups several tasks under one base scenario/world (e.g. `3d9a636_1/_2/_3`); each still runs as an independent session. |
| **tau2 domain / subset** | τ²-bench ships four domains (`mock`, `retail`, `airline`, `telecom`). We set **no** subset override, so every tau2 run here uses the library default — **`retail`**, 114 tasks. tau2 `task_id`s are that domain's own ids (`"0"`…`"113"`), so `task_id` 0–19 = the first 20 **retail** tasks. Domain is *not* recorded in the artifacts; it is only inferable from the absence of an override, so state it explicitly when quoting tau2 numbers. |


## 2. Column names & meaning

### `token_report.ndjson` / this report's per-task tables

| Column | Source field | Meaning |
|---|---|---|
| `task` | `task_id` | The benchmark task identifier (one row per task). |
| `in` | `llm_input_tokens` | Sum of `gen_ai.usage.input_tokens` over **all** LLM chat calls in that task. |
| `out` | `llm_output_tokens` | Sum of `gen_ai.usage.output_tokens` over all LLM chat calls in that task. |
| `total` | `llm_total_tokens` | `in + out` for the task. |
| `llm` | `llm_count` | Number of LLM (chat-completion) calls the agent made. One per `chat` span, counted one-for-one. |
| `tool` | `tool_count` | Number of MCP tool calls the agent made. |
| `passed` | `evaluation_result` | Task-level pass/fail from `evaluate_session` (`None` = errored before eval). |

`llm` and `tool` normally move together on these benchmarks: the agent takes a model turn, that turn
decides a tool call, and gsm8k submits its answer *through* a tool, so `llm == tool` is the healthy
shape rather than a coincidence.

**Rows marked ⚠ have lost token attribution** — the usage-bearing `chat` span was never written, so
`in`/`out` are understated while the task itself ran fine (`tool`, latency and `passed` are all
genuine). Two shapes are flagged, both of them structurally impossible rather than merely suspicious:

| flagged shape | why it cannot happen on a healthy task |
|---|---|
| `llm <= 1` with `tool >= 2` | every tool call needs a preceding model turn to decide it |
| `status == OK` with `llm = 0` | a task cannot complete without taking a single model turn |

**Do not substitute a `tokens == 0` test for either.** It is the intuitive check and it is unreliable:
a damaged row's token total depends on what other spans survived, so it can be non-zero, and a
zero-token row can equally be a task that failed before its first call. The shape of the counts is
the evidence; the token value is not.

The known trigger for lost spans is **reusing a warm agent across runs** — see
`docs/exgentic-agent-bug-report-20260901.md`. Every leg of this matrix therefore deploys a fresh
agent, which is why these columns can be read at face value here.

One caution when reading `llm`, `tool` or token totals **across** runs: they are properties of the
agent image as much as of the benchmark, and every agent image is pinned to `:latest`, so two runs
weeks apart can differ in call counts with nothing in the request changing. **No artifact in this set
records the agent digest** — not `run.json`, not `manifest.json` — so the only record is operational:
read `.status.containerStatuses[].imageID` off the agent pod while the run is live. Treat a
cross-run count comparison as unsupported unless you captured that.

If you did not, one weaker check is still available after the fact:
`skopeo inspect --raw docker://<agent image>:latest` gives the current index digest and its
per-platform children. That tells you what `:latest` points at **now**, so it can only confirm the tag
has *not* moved since your run — it can never recover the digest of a run that predates a push. Note
also that the index is multi-platform: compare the child for the cluster's architecture
(`linux/amd64` on OpenShift, `linux/arm64` on a KinD on Apple Silicon), and check the children share
`org.opencontainers.image.revision` before treating two clusters' legs as the same code.

### `span_report.ndjson` (§8)

One row per OTEL span per task — the evidence the counters above are derived from. Fields:
`task_id`, `session_id`, `trace_id`, `span_id`, `parent_span_id`, `name`, `kind`, `parent_name`,
`depth`, `start_time`, `latency_ms`, `status_code`, `error_type`, `counted`, `input_tokens`,
`output_tokens`, `request_model`, `request_max_tokens`, `finish_reasons`.

| Column | Meaning |
|---|---|
| `name` | The span title, e.g. `Agent.Session`, `chat gpt-5-mini`, `execute_tool submit`. |
| `kind` | `root` / `phase` / `agent` / `chat` / `tool` / `other` (`other` = A2A/HTTP framework internals inside the agent pod, named by neither the Service nor the agent). |
| `counted` | Whether this span fed `llm_count`/`tool_count`. `false` marks real work the aggregate cannot see, because only spans parented by `invoke_agent` are counted (and `execute_tool initial_observation` never is). `null` for non-chat/tool spans. |
| `request_max_tokens` | The `max_tokens` the agent asked for, `null` when it asked for none (the normal case). A value of `1` marks a capability probe rather than real work: agents up to `exgentic 0.3.5.dev131` issued one per task and it was counted as an LLM call, while `dev145` replaced it with an unbilled `GET /v1/models` check that emits no span. **This run set is from after that change**: none of its 1155 `chat` spans carries `max_tokens=1`, so its `llm` counts are real calls one-for-one with no offset to subtract. The column is the unambiguous way to tell a probe from a real call, and the probe's code path still exists upstream (`strict=True` in the agent's `health.py`), so it stays. |

That set is a deliberate whitelist: span attributes can carry prompts and completions, and these
objects are public, so nothing outside this list is published (see the S3 section).

### Run-summary fields (`RunSummary`, shown in §4)

| Field | Meaning |
|---|---|
| `status` | Terminal run status: `succeeded` / `failed` / `error` / `cancelled`. |
| `pass_rate` | `evaluated_pass / total` over the run's tasks. A task that never reached the model is in `total` and not in `evaluated_pass`, so this figure mixes "answered wrong" with "never ran" — see the `Probe` column and the note under §4's table. |
| `evaluated_pass` | Count of tasks that passed evaluation. |
| `total` | Number of task results recorded. |
| `Probe` | Not a `RunSummary` field — derived here by counting `run.json` results whose error is the agent's `GET /v1/models` health check. Such a task emits no spans, but it *does* leave a `report.ndjson` row (`status=ERROR`, `llm=0`, `tool=0`, zero tokens), so it is counted in `Err/Total` **and** included in every per-task token/latency statistic in §6–§7 — where it acts as a zero-cost outlier. `run.json` is used rather than `report.ndjson` because it has one result per task unconditionally. |
| `wall_seconds` | Wall-clock duration of the run. |


## 3. The 12 run requests to the AutoBench Service

Each run = an optional **deploy** step (teardown + `POST /benchmarks/<b>/deploy`) followed by a **run** step (`POST /benchmarks/<b>/runs`). Namespace is `team1`, agent is `tool_calling` throughout.

*Driver execution order was #1–3, #5–8, #4, #9–10, #11–12 (#4 last among gsm8k because it swaps the model); listed here in canonical order.*

### Run #1 — gsm8k — baseline (default model, 1 task, no gateway, no plugins)

- **Benchmark:** `gsm8k`
- **Model in use:** `openai/Azure/gpt-5-mini-2025-08-07`
- **Deploy:** `{"agent": "tool_calling", "namespace": "team1"}`
- **Run request** → `POST /benchmarks/gsm8k/runs`:
```json
{
  "agent": "tool_calling",
  "namespace": "team1",
  "max_tasks": 1,
  "max_parallel_sessions": 1,
  "timeout_seconds": 300
}
```

### Run #2 — gsm8k --max-tasks 10 — scale volume

- **Benchmark:** `gsm8k`
- **Model in use:** `openai/Azure/gpt-5-mini-2025-08-07`
- **Deploy:** `{"agent": "tool_calling", "namespace": "team1"}`
- **Run request** → `POST /benchmarks/gsm8k/runs`:
```json
{
  "agent": "tool_calling",
  "namespace": "team1",
  "max_tasks": 10,
  "max_parallel_sessions": 1,
  "timeout_seconds": 300
}
```

### Run #3 — gsm8k --max-tasks 50 --max-parallel-sessions 4 — add concurrency

- **Benchmark:** `gsm8k`
- **Model in use:** `openai/Azure/gpt-5-mini-2025-08-07`
- **Deploy:** `{"agent": "tool_calling", "namespace": "team1"}`
- **Run request** → `POST /benchmarks/gsm8k/runs`:
```json
{
  "agent": "tool_calling",
  "namespace": "team1",
  "max_tasks": 50,
  "max_parallel_sessions": 4,
  "timeout_seconds": 400
}
```

### Run #4 — gsm8k --model <alt> --max-tasks 5 --max-parallel-sessions 4 — swap model

- **Benchmark:** `gsm8k`
- **Model in use:** `openai/Azure/gpt-4.1`
- **Deploy:** `{"agent": "tool_calling", "namespace": "team1", "model": "openai/Azure/gpt-4.1"}`
- **Run request** → `POST /benchmarks/gsm8k/runs`:
```json
{
  "agent": "tool_calling",
  "namespace": "team1",
  "max_tasks": 5,
  "max_parallel_sessions": 4,
  "timeout_seconds": 300,
  "model": "openai/Azure/gpt-4.1",
  "task_timeout_seconds": 120
}
```

### Run #5 — gsm8k --plugin-preset auth-only — JWT + token exchange only

- **Benchmark:** `gsm8k`
- **Model in use:** `openai/Azure/gpt-5-mini-2025-08-07`
- **Deploy:** `{"agent": "tool_calling", "namespace": "team1", "authbridge_enabled": true, "plugin_preset": "auth-only"}`
- **Run request** → `POST /benchmarks/gsm8k/runs`:
```json
{
  "agent": "tool_calling",
  "namespace": "team1",
  "max_tasks": 5,
  "max_parallel_sessions": 4,
  "timeout_seconds": 300,
  "task_timeout_seconds": 120
}
```

### Run #6 — gsm8k --plugin-preset ibac-only — IBAC guardrails only

- **Benchmark:** `gsm8k`
- **Model in use:** `openai/Azure/gpt-5-mini-2025-08-07`
- **Deploy:** `{"agent": "tool_calling", "namespace": "team1", "authbridge_enabled": true, "plugin_preset": "ibac-only"}`
- **Run request** → `POST /benchmarks/gsm8k/runs`:
```json
{
  "agent": "tool_calling",
  "namespace": "team1",
  "max_tasks": 5,
  "max_parallel_sessions": 4,
  "timeout_seconds": 300,
  "task_timeout_seconds": 120
}
```

### Run #7 — gsm8k --plugin-preset full — auth + parsers + IBAC (enforce)

- **Benchmark:** `gsm8k`
- **Model in use:** `openai/Azure/gpt-5-mini-2025-08-07`
- **Deploy:** `{"agent": "tool_calling", "namespace": "team1", "authbridge_enabled": true, "plugin_preset": "full"}`
- **Run request** → `POST /benchmarks/gsm8k/runs`:
```json
{
  "agent": "tool_calling",
  "namespace": "team1",
  "max_tasks": 5,
  "max_parallel_sessions": 4,
  "timeout_seconds": 300,
  "task_timeout_seconds": 120
}
```

### Run #8 — gsm8k --plugin-preset full --plugin ibac:observe — full pipeline, IBAC observe

- **Benchmark:** `gsm8k`
- **Model in use:** `openai/Azure/gpt-5-mini-2025-08-07`
- **Deploy:** `{"agent": "tool_calling", "namespace": "team1", "authbridge_enabled": true, "plugin_preset": "full", "plugins": ["ibac:observe"]}`
- **Run request** → `POST /benchmarks/gsm8k/runs`:
```json
{
  "agent": "tool_calling",
  "namespace": "team1",
  "max_tasks": 5,
  "max_parallel_sessions": 4,
  "timeout_seconds": 300,
  "task_timeout_seconds": 120
}
```

### Run #9 — tau2 --max-tasks 10 — multi-turn (adds user-simulator LLM)

- **Benchmark:** `tau2`
- **Model in use:** `openai/aws/claude-sonnet-5`
- **Deploy:** `{"agent": "tool_calling", "namespace": "team1", "model": "openai/aws/claude-sonnet-5"}`
- **Run request** → `POST /benchmarks/tau2/runs`:
```json
{
  "agent": "tool_calling",
  "namespace": "team1",
  "max_tasks": 10,
  "max_parallel_sessions": 1,
  "timeout_seconds": 2100,
  "task_timeout_seconds": 600
}
```

### Run #10 — tau2 --max-tasks 20 --max-parallel-sessions 4 — tau2 + concurrency

- **Benchmark:** `tau2`
- **Model in use:** `openai/aws/claude-sonnet-5`
- **Deploy:** `{"agent": "tool_calling", "namespace": "team1", "model": "openai/aws/claude-sonnet-5"}`
- **Run request** → `POST /benchmarks/tau2/runs`:
```json
{
  "agent": "tool_calling",
  "namespace": "team1",
  "max_tasks": 20,
  "max_parallel_sessions": 4,
  "timeout_seconds": 2400,
  "task_timeout_seconds": 600
}
```

### Run #11 — appworld --model gemini-2.5-pro --max-tasks 5 — hardest benchmark

- **Benchmark:** `appworld`
- **Model in use:** `openai/gemini-2.5-pro`
- **Deploy:** `{"agent": "tool_calling", "namespace": "team1", "model": "openai/gemini-2.5-pro"}`
- **Run request** → `POST /benchmarks/appworld/runs`:
```json
{
  "agent": "tool_calling",
  "namespace": "team1",
  "max_tasks": 5,
  "max_parallel_sessions": 1,
  "timeout_seconds": 3300,
  "model": "openai/gemini-2.5-pro",
  "task_timeout_seconds": 600
}
```

### Run #12 — appworld --model gemini-2.5-pro --max-tasks 20 --max-parallel-sessions 4 — appworld + concurrency

- **Benchmark:** `appworld`
- **Model in use:** `openai/gemini-2.5-pro`
- **Deploy:** `{"agent": "tool_calling", "namespace": "team1", "model": "openai/gemini-2.5-pro"}`
- **Run request** → `POST /benchmarks/appworld/runs`:
```json
{
  "agent": "tool_calling",
  "namespace": "team1",
  "max_tasks": 20,
  "max_parallel_sessions": 4,
  "timeout_seconds": 3600,
  "model": "openai/gemini-2.5-pro",
  "task_timeout_seconds": 600
}
```


## 4. Summary of the 12 run results

| # | Benchmark | run_id | Model in use | Status | Pass | Eval-pass | Err/Total | Probe | Wall (s) |
|---|---|---|---|---|---:|---:|---:|---:|---:|
| 1 | gsm8k | `20261004200100-6a30e625` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 1 | 0/1 | 0 | 2 |
| 2 | gsm8k | `20261004202937-5d88290e` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 10 | 0/10 | 0 | 27 |
| 3 | gsm8k | `20261004204644-4de2c4cb` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 50 | 0/50 | 0 | 33 |
| 4 | gsm8k | `20261004213450-f08924a7` | openai/Azure/gpt-4.1 | succeeded | 0.8 | 4 | 0/5 | 0 | 8 |
| 5 | gsm8k | `20261004211545-446780bd` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 5 | 0/5 | 0 | 6 |
| 6 | gsm8k | `20261004213303-1537ae00` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 0.8 | 4 | 1/5 | 0 | 5 |
| 7 | gsm8k | `20261004215020-e6c27bfc` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 5 | 0/5 | 0 | 6 |
| 8 | gsm8k | `20261004220737-e1fbfbf9` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 5 | 0/5 | 0 | 9 |
| 9 | tau2 | `20261004211818-76eeb94f` | openai/aws/claude-sonnet-5 | succeeded | 1.0 | 10 | 0/10 | 0 | 500 |
| 10 | tau2 | `20261004203229-df8db303` | openai/aws/claude-sonnet-5 | succeeded | 0.85 | 17 | 0/20 | 0 | 266 |
| 11 | appworld | `20261004204927-5768d214` | openai/gemini-2.5-pro | succeeded | 0.0 | 0 | 0/5 | 0 | 1445 |
| 12 | appworld | `20261004200257-eb753a3f` | openai/gemini-2.5-pro | succeeded | 0.0 | 0 | 5/20 | 0 | 1484 |

**Tasks with no `report.ndjson` row: #12 (1 of 20).** These ended before the Service wrote a per-task row — on appworld, the per-task timeout. `Err/Total` counts them (it reads `run.json`, which has one result per task unconditionally), but **§6-§7 cannot**: a missing row contributes to no median, mean or CV, so those per-task statistics describe the tasks that finished rather than the tasks that were attempted.

**Token attribution:** complete — no row lost its usage-bearing span.

**Health probe:** every task reached the model — no task was lost to the agent's per-task `GET /v1/models` check.

**✅ These runs were spaced to defeat the gateway's completion cache.** The legs do repeat tasks — gsm8k #1/#2/#3/#5/#6/#7/#8 share 10 task ids; tau2 #9/#10 share 10 task ids; appworld on `openai/gemini-2.5-pro` #11/#12 share 5 task ids — but the driver rested every (benchmark, model) prompt set for at least **900 s** before reusing it, against a measured cache TTL of ~10 min, so **no leg could replay another's completions**: 4 leg(s) were the first of their prompt set and had nothing to replay, 4 had the gap covered by other legs running in between, and 4 waited out the remainder explicitly. The gap is measured from the previous leg's *finish*, which errs safe — a shared task set is always a prefix, so the colliding prompts were sent near that leg's start and are older still. Per-call latency and output tokens are therefore independent across these legs, which was not true of earlier matrices. For the underlying mechanism: A repeated request body comes back from `ete-litellm` as the *same stored response* — identical response `id`, identical `usage` — measured with the agent bypassed, on both clusters' gateways. **The TTL is ~10 minutes**, measured by survival curve (one probe per nonce at its own age: hit at 3/5/7/9 min, miss at 11/13/15/18/21). A replay re-reports the stored token counts and its latency is a cache lookup, so per-call latency and output token counts are affected; input tokens and pass rates are not (the same prompt and the same correct answer either way). This is not an agent setting — `EXGENTIC_LITELLM_CACHING=false` is pinned and provably inert against it — and **latency is not a reliable hit detector**: one measured replay took 3.0 s, the same as a miss. Compare response `id`s. Details in `docs/exgentic-agent-bug-report-20260901.md`.

## 5. Contents of the manifest file

Every run writes a `manifest.json` at its S3 prefix — a self-describing index of the run's data objects, so a client discovers the whole run in one fetch with no S3 listing. It lists the 7 data artifacts (`run.json`, `report.ndjson`, `token_report.ndjson`, `span_report.ndjson`, `report.parquet`, `token_report.parquet`, `span_report.parquet`); the manifest does **not** list itself. Each entry carries `name`, `format`, `key`, public `url`, and `size_bytes`.

Example:

```json
{
  "run_id": "20261004200100-6a30e625",
  "benchmark": "gsm8k",
  "prefix": "kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20261004200100-6a30e625",
  "artifacts": [
    {
      "name": "run.json",
      "format": "json",
      "key": "kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20261004200100-6a30e625/run.json",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20261004200100-6a30e625/run.json",
      "size_bytes": 470
    },
    {
      "name": "report.ndjson",
      "format": "ndjson",
      "key": "kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20261004200100-6a30e625/report.ndjson",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20261004200100-6a30e625/report.ndjson",
      "size_bytes": 1009
    },
    {
      "name": "token_report.ndjson",
      "format": "ndjson",
      "key": "kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20261004200100-6a30e625/token_report.ndjson",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20261004200100-6a30e625/token_report.ndjson",
      "size_bytes": 370
    },
    {
      "name": "span_report.ndjson",
      "format": "ndjson",
      "key": "kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20261004200100-6a30e625/span_report.ndjson",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20261004200100-6a30e625/span_report.ndjson",
      "size_bytes": 49444
    },
    {
      "name": "report.parquet",
      "format": "parquet",
      "key": "kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20261004200100-6a30e625/report.parquet",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20261004200100-6a30e625/report.parquet",
      "size_bytes": 11984
    },
    {
      "name": "token_report.parquet",
      "format": "parquet",
      "key": "kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20261004200100-6a30e625/token_report.parquet",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20261004200100-6a30e625/token_report.parquet",
      "size_bytes": 4425
    },
    {
      "name": "span_report.parquet",
      "format": "parquet",
      "key": "kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20261004200100-6a30e625/span_report.parquet",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20261004200100-6a30e625/span_report.parquet",
      "size_bytes": 10295
    }
  ]
}
```


## 6. Per-run aggregate — input & output tokens

`median` then `mean` then `CV` for each direction. median is the robust centre, mean the arithmetic average, and a mean well above the median signals right-skew (a few long tasks). `CV` is population sigma / mean, i.e. how unevenly the token cost is spread; it shares its denominator with `mean`, not `median`. CV is `—` for single-task runs.

A `⚠` in the Lost column means some of that run's rows lost their usage-bearing span (§2), so **every token figure on that row is understated** — including the median/mean/CV, which are computed over all tasks and so are dragged down by the damaged ones. Do not quote them as the run's cost.

| # | Benchmark | Model | Tasks | Lost | LLM calls | Input | Output | Total | IN median | IN mean | IN CV | OUT median | OUT mean | OUT CV |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 1 | 0 | 1 | 320 | 86 | 406 | 320 | 320 | — | 86 | 86 | — |
| 2 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 10 | 0 | 10 | 3166 | 1885 | 5051 | 312 | 317 | 0.08 | 86 | 188 | 0.98 |
| 3 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 50 | 0 | 50 | 15684 | 8974 | 24658 | 310 | 314 | 0.06 | 150 | 179 | 0.74 |
| 4 | gsm8k | openai/Azure/gpt-4.1 | 5 | 0 | 15 | 4177 | 313 | 4490 | 807 | 835 | 0.44 | 62 | 63 | 0.32 |
| 5 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 5 | 0 | 5 | 1564 | 1007 | 2571 | 306 | 313 | 0.09 | 86 | 201 | 0.99 |
| 6 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 5 | 0 | 5 | 1564 | 687 | 2251 | 306 | 313 | 0.09 | 86 | 137 | 0.55 |
| 7 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 5 | 0 | 5 | 1564 | 1391 | 2955 | 306 | 313 | 0.09 | 279 | 278 | 0.62 |
| 8 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 5 | 0 | 5 | 1564 | 1199 | 2763 | 306 | 313 | 0.09 | 86 | 240 | 0.93 |
| 9 | tau2 | openai/aws/claude-sonnet-5 | 10 | 0 | 110 | 916576 | 22330 | 938906 | 93134 | 91658 | 0.16 | 2302 | 2233 | 0.19 |
| 10 | tau2 | openai/aws/claude-sonnet-5 | 20 | 0 | 226 | 1726957 | 39532 | 1766489 | 90514 | 86348 | 0.21 | 1932 | 1977 | 0.31 |
| 11 | appworld | openai/gemini-2.5-pro | 5 | 0 | 159 | 1564276 | 143958 | 1708234 | 153615 | 312855 | 0.69 | 17131 | 28792 | 0.57 |
| 12 | appworld | openai/gemini-2.5-pro | 19 | 0 | 564 | 5349900 | 525001 | 5874901 | 223512 | 281574 | 0.67 | 24835 | 27632 | 0.52 |

## 7. Per-task detail

`model` is repeated on every row deliberately: token counts are only comparable within a model, and the runs do not all use the same one (#4 swaps the gsm8k model, so its ~815 input tokens per task are not comparable with the ~320 of #1–3/#5–8 on the same tasks).

`tool` is shown next to `llm` because their relationship is the tell for a damaged row: each tool call needs a preceding model turn, so `llm=1` beside `tool=11` cannot be real work — it is a lost span (§2), flagged `⚠`.

### Run #1 — gsm8k  ·  `20261004200100-6a30e625`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 86 | 406 | 1 | 1 | True |  |

### Run #2 — gsm8k  ·  `20261004202937-5d88290e`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 86 | 406 | 1 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 86 | 369 | 1 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 663 | 969 | 1 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 86 | 377 | 1 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 86 | 450 | 1 | 1 | True |  |
| 5 | openai/Azure/gpt-5-mini-2025-08-07 | 309 | 150 | 459 | 1 | 1 | True |  |
| 6 | openai/Azure/gpt-5-mini-2025-08-07 | 298 | 22 | 320 | 1 | 1 | True |  |
| 7 | openai/Azure/gpt-5-mini-2025-08-07 | 323 | 342 | 665 | 1 | 1 | True |  |
| 8 | openai/Azure/gpt-5-mini-2025-08-07 | 358 | 278 | 636 | 1 | 1 | True |  |
| 9 | openai/Azure/gpt-5-mini-2025-08-07 | 314 | 86 | 400 | 1 | 1 | True |  |

### Run #3 — gsm8k  ·  `20261004204644-4de2c4cb`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 342 | 662 | 1 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 86 | 369 | 1 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 279 | 585 | 1 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 150 | 441 | 1 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 86 | 450 | 1 | 1 | True |  |
| 5 | openai/Azure/gpt-5-mini-2025-08-07 | 309 | 150 | 459 | 1 | 1 | True |  |
| 6 | openai/Azure/gpt-5-mini-2025-08-07 | 298 | 86 | 384 | 1 | 1 | True |  |
| 7 | openai/Azure/gpt-5-mini-2025-08-07 | 323 | 278 | 601 | 1 | 1 | True |  |
| 8 | openai/Azure/gpt-5-mini-2025-08-07 | 358 | 214 | 572 | 1 | 1 | True |  |
| 9 | openai/Azure/gpt-5-mini-2025-08-07 | 314 | 86 | 400 | 1 | 1 | True |  |
| 10 | openai/Azure/gpt-5-mini-2025-08-07 | 315 | 86 | 401 | 1 | 1 | True |  |
| 11 | openai/Azure/gpt-5-mini-2025-08-07 | 316 | 86 | 402 | 1 | 1 | True |  |
| 12 | openai/Azure/gpt-5-mini-2025-08-07 | 322 | 214 | 536 | 1 | 1 | True |  |
| 13 | openai/Azure/gpt-5-mini-2025-08-07 | 315 | 278 | 593 | 1 | 1 | True |  |
| 14 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 86 | 392 | 1 | 1 | True |  |
| 15 | openai/Azure/gpt-5-mini-2025-08-07 | 347 | 150 | 497 | 1 | 1 | True |  |
| 16 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 598 | 904 | 1 | 1 | True |  |
| 17 | openai/Azure/gpt-5-mini-2025-08-07 | 309 | 215 | 524 | 1 | 1 | True |  |
| 18 | openai/Azure/gpt-5-mini-2025-08-07 | 284 | 278 | 562 | 1 | 1 | True |  |
| 19 | openai/Azure/gpt-5-mini-2025-08-07 | 321 | 150 | 471 | 1 | 1 | True |  |
| 20 | openai/Azure/gpt-5-mini-2025-08-07 | 317 | 278 | 595 | 1 | 1 | True |  |
| 21 | openai/Azure/gpt-5-mini-2025-08-07 | 301 | 86 | 387 | 1 | 1 | True |  |
| 22 | openai/Azure/gpt-5-mini-2025-08-07 | 311 | 86 | 397 | 1 | 1 | True |  |
| 23 | openai/Azure/gpt-5-mini-2025-08-07 | 293 | 406 | 699 | 1 | 1 | True |  |
| 24 | openai/Azure/gpt-5-mini-2025-08-07 | 292 | 150 | 442 | 1 | 1 | True |  |
| 25 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 86 | 406 | 1 | 1 | True |  |
| 26 | openai/Azure/gpt-5-mini-2025-08-07 | 321 | 86 | 407 | 1 | 1 | True |  |
| 27 | openai/Azure/gpt-5-mini-2025-08-07 | 310 | 86 | 396 | 1 | 1 | True |  |
| 28 | openai/Azure/gpt-5-mini-2025-08-07 | 304 | 86 | 390 | 1 | 1 | True |  |
| 29 | openai/Azure/gpt-5-mini-2025-08-07 | 324 | 150 | 474 | 1 | 1 | True |  |
| 30 | openai/Azure/gpt-5-mini-2025-08-07 | 292 | 86 | 378 | 1 | 1 | True |  |
| 31 | openai/Azure/gpt-5-mini-2025-08-07 | 317 | 150 | 467 | 1 | 1 | True |  |
| 32 | openai/Azure/gpt-5-mini-2025-08-07 | 297 | 150 | 447 | 1 | 1 | True |  |
| 33 | openai/Azure/gpt-5-mini-2025-08-07 | 285 | 86 | 371 | 1 | 1 | True |  |
| 34 | openai/Azure/gpt-5-mini-2025-08-07 | 297 | 86 | 383 | 1 | 1 | True |  |
| 35 | openai/Azure/gpt-5-mini-2025-08-07 | 305 | 86 | 391 | 1 | 1 | True |  |
| 36 | openai/Azure/gpt-5-mini-2025-08-07 | 297 | 86 | 383 | 1 | 1 | True |  |
| 37 | openai/Azure/gpt-5-mini-2025-08-07 | 315 | 214 | 529 | 1 | 1 | True |  |
| 38 | openai/Azure/gpt-5-mini-2025-08-07 | 300 | 150 | 450 | 1 | 1 | True |  |
| 39 | openai/Azure/gpt-5-mini-2025-08-07 | 329 | 150 | 479 | 1 | 1 | True |  |
| 40 | openai/Azure/gpt-5-mini-2025-08-07 | 308 | 342 | 650 | 1 | 1 | True |  |
| 41 | openai/Azure/gpt-5-mini-2025-08-07 | 379 | 150 | 529 | 1 | 1 | True |  |
| 42 | openai/Azure/gpt-5-mini-2025-08-07 | 336 | 22 | 358 | 1 | 1 | True |  |
| 43 | openai/Azure/gpt-5-mini-2025-08-07 | 310 | 150 | 460 | 1 | 1 | True |  |
| 44 | openai/Azure/gpt-5-mini-2025-08-07 | 326 | 214 | 540 | 1 | 1 | True |  |
| 45 | openai/Azure/gpt-5-mini-2025-08-07 | 350 | 342 | 692 | 1 | 1 | True |  |
| 46 | openai/Azure/gpt-5-mini-2025-08-07 | 346 | 726 | 1072 | 1 | 1 | True |  |
| 47 | openai/Azure/gpt-5-mini-2025-08-07 | 303 | 214 | 517 | 1 | 1 | True |  |
| 48 | openai/Azure/gpt-5-mini-2025-08-07 | 294 | 86 | 380 | 1 | 1 | True |  |
| 49 | openai/Azure/gpt-5-mini-2025-08-07 | 298 | 86 | 384 | 1 | 1 | True |  |

### Run #4 — gsm8k  ·  `20261004213450-f08924a7`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-4.1 | 807 | 50 | 857 | 3 | 3 | True |  |
| 1 | openai/Azure/gpt-4.1 | 471 | 62 | 533 | 2 | 2 | True |  |
| 2 | openai/Azure/gpt-4.1 | 1444 | 91 | 1535 | 5 | 5 | False |  |
| 3 | openai/Azure/gpt-4.1 | 451 | 33 | 484 | 2 | 2 | True |  |
| 4 | openai/Azure/gpt-4.1 | 1004 | 77 | 1081 | 3 | 3 | True |  |

### Run #5 — gsm8k  ·  `20261004211545-446780bd`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 150 | 470 | 1 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 86 | 369 | 1 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 599 | 905 | 1 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 86 | 377 | 1 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 86 | 450 | 1 | 1 | True |  |

### Run #6 — gsm8k  ·  `20261004213303-1537ae00`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 86 | 406 | 1 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 86 | 369 | 1 | 1 | None |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 279 | 585 | 1 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 150 | 441 | 1 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 86 | 450 | 1 | 1 | True |  |

### Run #7 — gsm8k  ·  `20261004215020-e6c27bfc`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 86 | 406 | 1 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 470 | 753 | 1 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 279 | 585 | 1 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 470 | 761 | 1 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 86 | 450 | 1 | 1 | True |  |

### Run #8 — gsm8k  ·  `20261004220737-e1fbfbf9`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 86 | 406 | 1 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 86 | 369 | 1 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 279 | 585 | 1 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 86 | 377 | 1 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 662 | 1026 | 1 | 1 | True |  |

### Run #9 — tau2  ·  `20261004211818-76eeb94f`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/aws/claude-sonnet-5 | 70708 | 1784 | 72492 | 10 | 10 | True |  |
| 1 | openai/aws/claude-sonnet-5 | 93108 | 1750 | 94858 | 12 | 12 | True |  |
| 2 | openai/aws/claude-sonnet-5 | 92981 | 1490 | 94471 | 11 | 11 | True |  |
| 3 | openai/aws/claude-sonnet-5 | 76134 | 2075 | 78209 | 9 | 9 | True |  |
| 4 | openai/aws/claude-sonnet-5 | 77617 | 2310 | 79927 | 9 | 9 | True |  |
| 5 | openai/aws/claude-sonnet-5 | 124827 | 2650 | 127477 | 14 | 14 | True |  |
| 6 | openai/aws/claude-sonnet-5 | 93910 | 2527 | 96437 | 11 | 11 | True |  |
| 7 | openai/aws/claude-sonnet-5 | 100372 | 2806 | 103178 | 12 | 12 | True |  |
| 8 | openai/aws/claude-sonnet-5 | 93161 | 2294 | 95455 | 11 | 11 | True |  |
| 9 | openai/aws/claude-sonnet-5 | 93758 | 2644 | 96402 | 11 | 11 | True |  |

### Run #10 — tau2  ·  `20261004203229-df8db303`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/aws/claude-sonnet-5 | 92717 | 2295 | 95012 | 12 | 12 | True |  |
| 1 | openai/aws/claude-sonnet-5 | 106301 | 1882 | 108183 | 15 | 13 | True |  |
| 2 | openai/aws/claude-sonnet-5 | 88312 | 1793 | 90105 | 10 | 10 | False |  |
| 3 | openai/aws/claude-sonnet-5 | 98730 | 1726 | 100456 | 12 | 12 | True |  |
| 4 | openai/aws/claude-sonnet-5 | 64189 | 2421 | 66610 | 8 | 8 | True |  |
| 5 | openai/aws/claude-sonnet-5 | 117311 | 2260 | 119571 | 13 | 13 | True |  |
| 6 | openai/aws/claude-sonnet-5 | 93323 | 2322 | 95645 | 11 | 11 | True |  |
| 7 | openai/aws/claude-sonnet-5 | 93086 | 2440 | 95526 | 11 | 11 | True |  |
| 8 | openai/aws/claude-sonnet-5 | 93198 | 2085 | 95283 | 11 | 11 | True |  |
| 9 | openai/aws/claude-sonnet-5 | 118108 | 3294 | 121402 | 13 | 13 | True |  |
| 10 | openai/aws/claude-sonnet-5 | 54120 | 1161 | 55281 | 9 | 9 | True |  |
| 11 | openai/aws/claude-sonnet-5 | 73623 | 1981 | 75604 | 11 | 11 | True |  |
| 12 | openai/aws/claude-sonnet-5 | 57059 | 1364 | 58423 | 9 | 9 | True |  |
| 13 | openai/aws/claude-sonnet-5 | 82812 | 1612 | 84424 | 12 | 12 | True |  |
| 14 | openai/aws/claude-sonnet-5 | 66892 | 1138 | 68030 | 10 | 10 | True |  |
| 15 | openai/aws/claude-sonnet-5 | 106069 | 1813 | 107882 | 14 | 13 | True |  |
| 16 | openai/aws/claude-sonnet-5 | 77753 | 2254 | 80007 | 10 | 10 | True |  |
| 17 | openai/aws/claude-sonnet-5 | 66936 | 961 | 67897 | 11 | 11 | True |  |
| 18 | openai/aws/claude-sonnet-5 | 97518 | 1477 | 98995 | 14 | 14 | False |  |
| 19 | openai/aws/claude-sonnet-5 | 78900 | 3253 | 82153 | 10 | 10 | False |  |

### Run #11 — appworld  ·  `20261004204927-5768d214`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 3d9a636_1 | openai/gemini-2.5-pro | 150587 | 16013 | 166600 | 20 | 10 | False |  |
| 3d9a636_2 | openai/gemini-2.5-pro | 153615 | 17131 | 170746 | 21 | 10 | False |  |
| 3d9a636_3 | openai/gemini-2.5-pro | 109658 | 13224 | 122882 | 16 | 7 | False |  |
| fd1f8fa_1 | openai/gemini-2.5-pro | 559391 | 45728 | 605119 | 50 | 25 | False |  |
| fd1f8fa_2 | openai/gemini-2.5-pro | 591025 | 51862 | 642887 | 52 | 26 | False |  |

### Run #12 — appworld  ·  `20261004200257-eb753a3f`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 3d9a636_1 | openai/gemini-2.5-pro | 132974 | 16336 | 149310 | 16 | 8 | False |  |
| 3d9a636_2 | openai/gemini-2.5-pro | 170756 | 22494 | 193250 | 22 | 11 | False |  |
| 3d9a636_3 | openai/gemini-2.5-pro | 193859 | 19441 | 213300 | 24 | 12 | False |  |
| 21abae1_1 | openai/gemini-2.5-pro | 63581 | 8283 | 71864 | 10 | 5 | False |  |
| 21abae1_2 | openai/gemini-2.5-pro | 63107 | 8982 | 72089 | 10 | 5 | False |  |
| 21abae1_3 | openai/gemini-2.5-pro | 103656 | 15199 | 118855 | 16 | 8 | False |  |
| 29a7b7e_1 | openai/gemini-2.5-pro | 352816 | 41402 | 394218 | 40 | 19 | None |  |
| 29a7b7e_2 | openai/gemini-2.5-pro | 223512 | 26882 | 250394 | 28 | 13 | None |  |
| 29a7b7e_3 | openai/gemini-2.5-pro | 181167 | 27341 | 208508 | 20 | 10 | False |  |
| 325d6ec_1 | openai/gemini-2.5-pro | 261781 | 22335 | 284116 | 36 | 18 | False |  |
| 325d6ec_2 | openai/gemini-2.5-pro | 119845 | 13602 | 133447 | 18 | 9 | False |  |
| 325d6ec_3 | openai/gemini-2.5-pro | 277864 | 24835 | 302699 | 36 | 18 | False |  |
| 634f342_1 | openai/gemini-2.5-pro | 467602 | 37708 | 505310 | 44 | 22 | False |  |
| 634f342_3 | openai/gemini-2.5-pro | 711419 | 57792 | 769211 | 70 | 35 | None |  |
| 8749218_1 | openai/gemini-2.5-pro | 134416 | 18588 | 153004 | 16 | 7 | None |  |
| 8749218_2 | openai/gemini-2.5-pro | 442821 | 31316 | 474137 | 28 | 14 | False |  |
| fd1f8fa_1 | openai/gemini-2.5-pro | 522058 | 54991 | 577049 | 48 | 24 | False |  |
| fd1f8fa_2 | openai/gemini-2.5-pro | 266531 | 26846 | 293377 | 26 | 13 | False |  |
| fd1f8fa_3 | openai/gemini-2.5-pro | 660135 | 50628 | 710763 | 56 | 28 | False |  |


## 8. Per-task span inventory

Which spans each task actually invoked, by name. §6 and §7 report *counts*; this is what they were counted from, read straight out of each run's `span_report.ndjson`.

Read the **chat** and **tool** columns together. There is no fixed healthy chat count — a one-shot gsm8k task legitimately shows a single `chat` span, and a multi-turn tau2 task shows many. What is not possible is **`chat` ≤ 1 alongside `tool` ≥ 2**: every tool call needs a model turn to request it, so a task cannot invoke two tools off one chat span. That shape means a usage-bearing span was dropped, and this section flags it. Reading it off *counts* rather than token values is what makes it catchable on every model, including ones whose dropped span would still have reported plausible non-zero usage.

⚠ **The `chat` and `tool` columns below are raw span totals; the ⚠ flag is computed on the `counted` subset only.** Do not apply the rule by hand to these columns. A healthy gsm8k task emits two `execute_tool` spans — `initial_observation` and `submit` — of which only `submit` is counted, so its raw pair is `1`/`2` and would trip the test while its §7 pair is the innocent `1`/`1`. Filtering to `counted` is what makes this section agree with §7.

Up to `exgentic 0.3.5.dev131` each task also issued a `max_tokens=1` capability probe, so a bare `chat == 1` used to be the damage signal and **at least 2** was healthy. The probe was replaced in `dev145` by an unbilled `GET /v1/models` check that emits no span — do not resurrect that rule, and do not compare chat counts across runs that straddle the change. **This run set is from after that change**: none of its 1155 `chat` spans carries `max_tokens=1`, so its `llm` counts are real calls one-for-one with no offset to subtract.

The **select** column splits the chat spans by *what the call was for*. The agent image defaults to `enable_tool_shortlisting = True, max_selected_tools = 30`: when the MCP advertises more tools than that, every turn opens with an **extra** LLM call that carries the whole tool inventory — each name and description — and asks the model to rank it, after which only the winners' schemas go into the call that decides the action. Both kinds land in `llm_count`, so §6 and §7 cannot separate them and the spans are the only place this is visible.

| benchmark | tasks | LLM calls / task | of which select | input tokens spent selecting | output tokens spent selecting |
|---|---:|---:|---:|---:|---:|
| gsm8k | 86 | 1.1 | 0.0 (0%) | 0% | 0% |
| tau2 | 30 | 11.1 | 0.0 (0%) | 0% | 0% |
| appworld | 24 | 30.0 | 15.0 (50%) | 67% | 79% |

`gsm8k` and `tau2` measure **exactly zero** — they expose fewer tools than the threshold, so shortlisting cannot fire on them, and that is the control for the classifier rather than a dull row: it is what shows ordinary multi-turn traffic is not being counted as selection. `appworld` pairs **1:1** (360 selection calls against 360 assistant calls), and the selection call is the dearer half — **67% of its input tokens** — because it carries every name and description while the assistant call carries only 30 schemas. Read that benchmark's token and cost figures accordingly, and note the model never sees more than 30 of its tools at once.

The count excludes `max_tokens=1` capability probes (they are not turns) and is computed per task from span *order*, never from token size: a `chat` span immediately followed by another `chat` is a selection call, one followed by a tool span is the assistant call that requested that tool. So it is the `chat` column, not the raw span total, that the `select` column is a subset of.

`not counted` are spans the aggregator cannot see: it only folds a chat/tool span into `llm_count`/`tool_count` when its parent is the `invoke_agent` span, so anything nested deeper is real work missing from the totals. A non-zero figure there is not a bug by itself — it is the known blind spot, now measurable.

The **names** column lists only the spans the Service names (`root`/`phase`) and those the agent names (`agent`/`chat`/`tool`). The `other` column counts the rest — A2A/HTTP framework internals inside the agent pod, such as `EventQueue.dequeue_event` or the ASGI `POST / http send`, which dominate the raw count (a single gsm8k task emits ~98 spans, ~90 of them framework noise) and would swamp this table. They are all present in `span_report.ndjson`; this section is the readable summary, not the full tree.

### Run #1 — gsm8k  ·  `20261004200100-6a30e625`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #2 — gsm8k  ·  `20261004202937-5d88290e`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 102 | 1 | 0 | 2 | 94 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 5 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 6 | 92 | 1 | 0 | 2 | 84 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 7 | 96 | 1 | 0 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 8 | 96 | 1 | 0 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 9 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #3 — gsm8k  ·  `20261004204644-4de2c4cb`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 97 | 1 | 0 | 2 | 89 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 96 | 1 | 0 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 5 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 6 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 7 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 8 | 95 | 1 | 0 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 9 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 10 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 11 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 12 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 13 | 96 | 1 | 0 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 14 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 15 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 16 | 100 | 1 | 0 | 2 | 92 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 17 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 18 | 95 | 1 | 0 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 19 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 20 | 96 | 1 | 0 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 21 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 22 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 23 | 98 | 1 | 0 | 2 | 90 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 24 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 25 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 26 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 27 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 28 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 29 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 30 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 31 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 32 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 33 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 34 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 35 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 36 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 37 | 97 | 1 | 0 | 2 | 89 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 38 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 39 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 40 | 96 | 1 | 0 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 41 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 42 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 43 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 44 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 45 | 96 | 1 | 0 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 46 | 101 | 1 | 0 | 2 | 93 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 47 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 48 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 49 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #4 — gsm8k  ·  `20261004213450-f08924a7`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 127 | 3 | 0 | 4 | 115 | 1 | `chat Azure/gpt-4.1` x3, `execute_tool calculate_expression` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 112 | 2 | 0 | 3 | 102 | 1 | `chat Azure/gpt-4.1` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool calculate_expression`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 145 | 5 | 0 | 6 | 129 | 1 | `chat Azure/gpt-4.1` x5, `execute_tool calculate_expression` x4, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 105 | 2 | 0 | 3 | 95 | 1 | `chat Azure/gpt-4.1` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool calculate_expression`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 124 | 3 | 0 | 4 | 112 | 1 | `chat Azure/gpt-4.1` x3, `execute_tool calculate_expression` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #5 — gsm8k  ·  `20261004211545-446780bd`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 99 | 1 | 0 | 2 | 91 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 92 | 1 | 0 | 2 | 84 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #6 — gsm8k  ·  `20261004213303-1537ae00`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 96 | 1 | 0 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 95 | 1 | 0 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit` |
| 2 | 97 | 1 | 0 | 2 | 89 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 95 | 1 | 0 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 92 | 1 | 0 | 2 | 84 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #7 — gsm8k  ·  `20261004215020-e6c27bfc`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 96 | 1 | 0 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 98 | 1 | 0 | 2 | 90 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 97 | 1 | 0 | 2 | 89 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 98 | 1 | 0 | 2 | 90 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 92 | 1 | 0 | 2 | 84 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #8 — gsm8k  ·  `20261004220737-e1fbfbf9`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 103 | 1 | 0 | 2 | 95 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 96 | 1 | 0 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 97 | 1 | 0 | 2 | 89 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 95 | 1 | 0 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 99 | 1 | 0 | 2 | 91 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #9 — tau2  ·  `20261004211818-76eeb94f`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 276 | 10 | 0 | 11 | 250 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x4, `execute_tool get_order_details` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 1 | 305 | 12 | 0 | 13 | 275 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x6, `execute_tool get_order_details` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 2 | 294 | 11 | 0 | 12 | 266 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 3 | 281 | 9 | 0 | 10 | 257 | 1 | `chat aws/claude-sonnet-5` x9, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 4 | 291 | 9 | 0 | 10 | 267 | 1 | `chat aws/claude-sonnet-5` x9, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 5 | 364 | 14 | 0 | 15 | 330 | 1 | `chat aws/claude-sonnet-5` x14, `execute_tool message` x9, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 6 | 312 | 11 | 0 | 12 | 284 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 7 | 333 | 12 | 0 | 13 | 303 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 8 | 320 | 11 | 0 | 12 | 292 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 9 | 309 | 11 | 0 | 12 | 281 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |

### Run #10 — tau2  ·  `20261004203229-df8db303`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 314 | 12 | 0 | 13 | 284 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x6, `execute_tool get_order_details` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 1 | 325 | 15 | 0 | 14 | 291 | 1 | `chat aws/claude-sonnet-5` x15, `execute_tool message` x8, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool get_user_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 2 | 294 | 10 | 0 | 11 | 268 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 3 | 311 | 12 | 0 | 13 | 281 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 4 | 275 | 8 | 0 | 9 | 253 | 1 | `chat aws/claude-sonnet-5` x8, `execute_tool message` x4, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 5 | 336 | 13 | 0 | 14 | 304 | 1 | `chat aws/claude-sonnet-5` x13, `execute_tool message` x8, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 6 | 313 | 11 | 0 | 12 | 285 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 7 | 317 | 11 | 0 | 12 | 289 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 8 | 289 | 11 | 0 | 12 | 261 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 9 | 364 | 13 | 0 | 14 | 332 | 1 | `chat aws/claude-sonnet-5` x13, `execute_tool message` x8, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 10 | 245 | 9 | 0 | 10 | 221 | 1 | `chat aws/claude-sonnet-5` x9, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool transfer_to_human_agents`, `Evaluator.Evaluate` |
| 11 | 286 | 11 | 0 | 12 | 258 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 12 | 249 | 9 | 0 | 10 | 225 | 1 | `chat aws/claude-sonnet-5` x9, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool transfer_to_human_agents`, `Evaluator.Evaluate` |
| 13 | 301 | 12 | 0 | 13 | 271 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x8, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 14 | 259 | 10 | 0 | 11 | 233 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 15 | 317 | 14 | 0 | 14 | 284 | 1 | `chat aws/claude-sonnet-5` x14, `execute_tool message` x8, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 16 | 293 | 10 | 0 | 11 | 267 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool cancel_pending_order`, `Evaluator.Evaluate` |
| 17 | 259 | 11 | 0 | 12 | 231 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `execute_tool get_order_details` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool modify_pending_order_address`, `Evaluator.Evaluate` |
| 18 | 325 | 14 | 0 | 15 | 291 | 1 | `chat aws/claude-sonnet-5` x14, `execute_tool message` x10, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 19 | 304 | 10 | 0 | 11 | 278 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x4, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool return_delivered_order_items`, `execute_tool transfer_to_human_agents`, `Evaluator.Evaluate` |

### Run #11 — appworld  ·  `20261004204927-5768d214`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 3d9a636_1 | 600 | 20 | 10 | 11 | 564 | 1 | `chat gemini-2.5-pro` x20, `execute_tool phone__login` x2, `execute_tool phone__search_contacts` x2, `execute_tool venmo__search_friends` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__add_friend`, `execute_tool venmo__remove_friend`, `execute_tool finish`, `Evaluator.Evaluate` |
| 3d9a636_2 | 615 | 21 | 10 | 11 | 578 | 1 | `chat gemini-2.5-pro` x21, `execute_tool phone__search_contacts` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool supervisor__show_profile`, `execute_tool phone__login`, `execute_tool phone__show_contact_relationships`, `execute_tool venmo__remove_friend`, `execute_tool venmo__add_friend`, `execute_tool finish`, `Evaluator.Evaluate` |
| 3d9a636_3 | 509 | 16 | 7 | 8 | 480 | 1 | `chat gemini-2.5-pro` x16, `execute_tool phone__search_contacts` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool phone__login`, `execute_tool venmo__add_friend`, `execute_tool venmo__remove_friend`, `execute_tool finish`, `Evaluator.Evaluate` |
| fd1f8fa_1 | 1269 | 50 | 25 | 26 | 1188 | 1 | `chat gemini-2.5-pro` x50, `execute_tool spotify__remove_song_from_queue` x11, `execute_tool spotify__play_music` x6, `execute_tool spotify__login` x2, `execute_tool spotify__show_liked_songs` x2, `execute_tool spotify__show_song_queue` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool finish`, `Evaluator.Evaluate` |
| fd1f8fa_2 | 1448 | 52 | 26 | 27 | 1364 | 1 | `chat gemini-2.5-pro` x52, `execute_tool spotify__remove_song_from_queue` x12, `execute_tool spotify__play_music` x7, `execute_tool spotify__show_song_queue` x2, `execute_tool spotify__show_liked_songs` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool finish`, `Evaluator.Evaluate` |

### Run #12 — appworld  ·  `20261004200257-eb753a3f`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 3d9a636_1 | 554 | 16 | 8 | 9 | 524 | 1 | `chat gemini-2.5-pro` x16, `execute_tool phone__login` x2, `execute_tool venmo__search_friends` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__add_friend`, `execute_tool venmo__remove_friend`, `execute_tool finish`, `Evaluator.Evaluate` |
| 3d9a636_2 | 692 | 22 | 11 | 12 | 653 | 1 | `chat gemini-2.5-pro` x22, `execute_tool venmo__search_friends` x3, `execute_tool phone__search_contacts` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool phone__login`, `execute_tool phone__show_contact_relationships`, `execute_tool venmo__add_friend`, `execute_tool venmo__remove_friend`, `execute_tool finish`, `Evaluator.Evaluate` |
| 3d9a636_3 | 652 | 24 | 12 | 13 | 610 | 1 | `chat gemini-2.5-pro` x24, `execute_tool phone__search_contacts` x3, `execute_tool venmo__search_friends` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool phone__show_contact_relationships`, `execute_tool venmo__add_friend`, `execute_tool venmo__remove_friend`, `execute_tool finish`, `Evaluator.Evaluate` |
| 21abae1_1 | 314 | 10 | 5 | 6 | 293 | 1 | `chat gemini-2.5-pro` x10, `execute_tool venmo__show_transactions` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| 21abae1_2 | 320 | 10 | 5 | 6 | 299 | 1 | `chat gemini-2.5-pro` x10, `execute_tool venmo__show_transactions` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| 21abae1_3 | 470 | 16 | 8 | 9 | 440 | 1 | `chat gemini-2.5-pro` x16, `execute_tool venmo__show_transactions` x4, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool phone__get_current_date_and_time`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| 29a7b7e_1 | 1069 | 40 | 20 | 20 | 1005 | 1 | `chat gemini-2.5-pro` x40, `execute_tool file_system__move_file` x13, `execute_tool file_system__create_directory` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_profile`, `execute_tool supervisor__show_account_passwords`, `execute_tool file_system__login`, `execute_tool file_system__show_directory` |
| 29a7b7e_2 | 742 | 28 | 14 | 14 | 696 | 1 | `chat gemini-2.5-pro` x28, `execute_tool file_system__move_file` x8, `execute_tool file_system__create_directory` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool file_system__login`, `execute_tool file_system__show_directory` |
| 29a7b7e_3 | 774 | 20 | 10 | 11 | 738 | 1 | `chat gemini-2.5-pro` x20, `execute_tool file_system__move_file` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool file_system__login`, `execute_tool file_system__show_directory`, `execute_tool file_system__create_directory`, `execute_tool finish`, `Evaluator.Evaluate` |
| 325d6ec_1 | 742 | 36 | 18 | 19 | 682 | 1 | `chat gemini-2.5-pro` x36, `execute_tool spotify__show_song_privates` x7, `execute_tool spotify__previous_song` x5, `execute_tool spotify__login` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__show_current_song`, `execute_tool spotify__show_song`, `execute_tool finish`, `Evaluator.Evaluate` |
| 325d6ec_2 | 453 | 18 | 9 | 10 | 420 | 1 | `chat gemini-2.5-pro` x18, `execute_tool spotify__next_song` x4, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool spotify__show_downloaded_songs`, `execute_tool spotify__show_current_song`, `execute_tool finish`, `Evaluator.Evaluate` |
| 325d6ec_3 | 794 | 36 | 18 | 19 | 734 | 1 | `chat gemini-2.5-pro` x36, `execute_tool spotify__show_current_song` x5, `execute_tool spotify__show_song_privates` x5, `execute_tool spotify__next_song` x4, `execute_tool spotify__login` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool finish`, `Evaluator.Evaluate` |
| 634f342_1 | 1193 | 44 | 22 | 23 | 1121 | 1 | `chat gemini-2.5-pro` x44, `execute_tool spotify__remove_song_from_playlist` x5, `execute_tool spotify__show_playlist_library` x3, `execute_tool file_system__login` x2, `execute_tool file_system__show_file` x2, `execute_tool file_system__show_directory` x2, `execute_tool spotify__show_playlist` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool supervisor__show_profile`, `execute_tool spotify__login`, `execute_tool spotify__create_playlist`, `execute_tool spotify__add_song_to_playlist`, `execute_tool finish`, `Evaluator.Evaluate` |
| 634f342_3 | 1680 | 70 | 35 | 36 | 1570 | 1 | `chat gemini-2.5-pro` x70, `execute_tool spotify__show_song` x16, `execute_tool spotify__remove_song_from_playlist` x6, `execute_tool spotify__add_song_to_playlist` x5, `execute_tool spotify__show_playlist_library` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool file_system__login`, `execute_tool file_system__show_file`, `execute_tool spotify__login`, `execute_tool spotify__create_playlist`, `execute_tool spotify__search_song` |
| 8749218_1 | 635 | 16 | 8 | 8 | 607 | 1 | `chat gemini-2.5-pro` x16, `execute_tool spotify__show_recommendations` x2, `execute_tool spotify__add_to_queue` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool spotify__clear_song_queue` |
| 8749218_2 | 1162 | 28 | 14 | 15 | 1114 | 1 | `chat gemini-2.5-pro` x28, `execute_tool spotify__add_to_queue` x3, `execute_tool spotify__show_recommendations` x2, `execute_tool spotify__play_music` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool spotify__clear_song_queue`, `execute_tool spotify__create_playlist`, `execute_tool spotify__add_song_to_playlist`, `execute_tool spotify__show_playlist`, `execute_tool finish`, `Evaluator.Evaluate` |
| fd1f8fa_1 | 1384 | 48 | 24 | 25 | 1306 | 1 | `chat gemini-2.5-pro` x48, `execute_tool spotify__remove_song_from_queue` x11, `execute_tool spotify__play_music` x3, `execute_tool spotify__show_liked_songs` x2, `execute_tool phone__show_text_message` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool spotify__show_song_queue`, `execute_tool spotify__seek_song`, `execute_tool spotify__show_current_song`, `execute_tool finish`, `Evaluator.Evaluate` |
| fd1f8fa_2 | 772 | 26 | 13 | 14 | 727 | 1 | `chat gemini-2.5-pro` x26, `execute_tool spotify__remove_song_from_queue` x3, `execute_tool spotify__play_music` x3, `execute_tool spotify__show_song_queue` x2, `execute_tool spotify__show_liked_songs` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| fd1f8fa_3 | 1351 | 56 | 28 | 29 | 1261 | 1 | `chat gemini-2.5-pro` x56, `execute_tool spotify__remove_song_from_queue` x11, `execute_tool spotify__play_music` x9, `execute_tool spotify__show_liked_songs` x3, `execute_tool spotify__show_song_queue` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool finish`, `Evaluator.Evaluate` |


## Reproducing this report

```sh
python3 reference/gen-12run-report.py /tmp/autobench/run12-kind-v135-20261004.json v1.35 "KinD — single-node local, Helm install, agent runner direct (ETE-ext gateway)" docs/results/v1.35-2026-10-04/12run-kind.md
```

The run JSON and the mirrored artifacts it points at are produced by `reference/run-12.py`; if `/tmp` has been pruned since, re-hydrate the mirror with `reference/remirror.py` first — the generators treat a missing artifact as an empty one and will quietly emit a much shorter report.
