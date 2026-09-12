# AutoBench Service — 12 Parameterized Runs (KinD — single-node local cluster)

**Report generated:** 2026-09-12T19:09:49Z  
**Service version:** `v1.27`  
**Platform:** KinD — single-node local cluster  
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

Objects are readable **and listable anonymously** (no credentials needed), so treat anything written here as public. What is actually exposed: the **keys** carry the caller's username and the Keycloak issuer host, and `run.json` / `report.ndjson` can carry **exception strings** from failed tasks (`status_message`, `TaskResult.error`), which may quote a server error body. No artifact contains task prompts or model outputs — the record schema is entirely ids, counts and durations, and `span_report.*` is restricted to a fixed whitelist of structural and numeric span fields for exactly this reason.

## 1. Terms

| Term | Meaning |
|---|---|
| **Benchmark** | A named evaluation suite (`gsm8k`, `tau2`, `appworld`), each backed by an agent + an MCP tool service. |
| **Run** | One invocation of `run_benchmark` against a deployed benchmark (`POST /benchmarks/<b>/runs`). Identified by a `run_id`. A run selects the first `max_tasks` tasks and executes them, up to `max_parallel_sessions` at a time. |
| **Task** | One self-contained benchmark item. Each task runs in **complete isolation**: its own MCP session (`create_session`), its own prompt, one agent call, its own evaluation (`evaluate_session`), and its session is deleted afterward. |
| **Span** | The unit of OTEL instrumentation: one timed operation with a `name`, a parent, a status and attributes. A task's spans form a **tree** rooted at its `Agent.Session` span, and every number in §6/§7 is computed from them — `llm_count` counts `chat` spans, `tool_count` counts `execute_tool` spans, token totals come from `gen_ai.usage.*` on the `chat` spans. Only a handful are named by the harness (`Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent…`, `chat <model>`, `execute_tool <tool>`, `Evaluator.Evaluate`); the rest are A2A/HTTP framework internals and dominate the raw count — a single gsm8k task emits ~100 spans, a tau2 task ~400, an appworld task ~800. **Every span is now published per task in `span_report.ndjson`** and summarised in §8, so the aggregate counters can be audited rather than trusted. |
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
| `llm` | `llm_count` | Number of LLM (chat-completion) calls the agent made. One per `chat` span. **Overcounts real calls by 1** — see below. |
| `tool` | `tool_count` | Number of MCP tool calls the agent made. |
| `passed` | `evaluation_result` | Task-level pass/fail from `evaluate_session` (`None` = errored before eval). |

**Every task issues one extra `max_tokens=1` capability probe**, counted as a `chat` span. So `llm`
reads one high: `llm=2` on gsm8k means *one* real call. Whether that probe succeeds is
model-dependent, which matters for the next paragraph.

**Rows marked ⚠ have lost token attribution** — the usage-bearing `chat` span for the real call was
never written, so `in`/`out` are understated (the task itself ran fine: `tool`, latency and
`passed` are all genuine). The token value alone does not identify these, because the same defect
presents differently per model:

| model class | probe outcome | damaged row looks like |
|---|---|---|
| reasoning (`gpt-5-mini`) | rejected, `BadRequestError`, no usage | `in=0, out=0` — the classic "zero-token row" |
| non-reasoning (`claude-sonnet-5`) | **succeeds**, so the probe's own usage is recorded | `in=8, out=1` |
| non-reasoning (`gemini-2.5-pro`) | **succeeds** | `in=1, out=0` |

A `tokens == 0` test therefore silently misses the sonnet-5/gemini cases. The detector used here is
structural instead: **`llm<=1` together with `tool>=2` is impossible**, since every tool call needs
a preceding model turn. Trigger: reusing a warm agent across runs (upstream exgentic tears down the
per-process OTEL context at run end) — see `docs/exgentic-agent-bug-report-20260901.md`. Mitigation
is a fresh deploy per run. `llm=0` with `tool=0` is different and benign-ish: no chat span at all
(session rejected before any model call).

### `span_report.ndjson` (§8)

One row per OTEL span per task — the evidence the counters above are derived from. Fields:
`task_id`, `session_id`, `trace_id`, `span_id`, `parent_span_id`, `name`, `kind`, `parent_name`,
`depth`, `start_time`, `latency_ms`, `status_code`, `error_type`, `counted`, `input_tokens`,
`output_tokens`, `request_model`, `request_max_tokens`, `finish_reasons`.

| Column | Meaning |
|---|---|
| `name` | The span title, e.g. `Agent.Session`, `chat gpt-5-mini`, `execute_tool submit`. |
| `kind` | `root` / `phase` / `agent` / `chat` / `tool` / `other` (`other` = nested HTTP/framework children the harness does not name). |
| `counted` | Whether this span fed `llm_count`/`tool_count`. `false` marks real work the aggregate cannot see, because only spans parented by `invoke_agent` are counted (and `execute_tool initial_observation` never is). `null` for non-chat/tool spans. |
| `request_max_tokens` | `1` identifies the capability probe unambiguously. |

That set is a deliberate whitelist: span attributes can carry prompts and completions, and these
objects are public, so nothing outside this list is published (see the S3 section).

### Run-summary fields (`RunSummary`, shown in §4)

| Field | Meaning |
|---|---|
| `status` | Terminal run status: `succeeded` / `failed` / `error` / `cancelled`. |
| `pass_rate` | `evaluated_pass / total` over the run's tasks. |
| `evaluated_pass` | Count of tasks that passed evaluation. |
| `total` | Number of task results recorded. |
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
  "timeout_seconds": 120
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
- **Deploy:** `{"agent": "tool_calling", "namespace": "team1"}`
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
- **Deploy:** `{"agent": "tool_calling", "namespace": "team1"}`
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

| # | Benchmark | run_id | Model in use | Status | Pass | Eval-pass | Err/Total | Wall (s) |
|---|---|---|---|---|---:|---:|---:|---:|
| 1 | gsm8k | `20260912022127-960a861d` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 1 | 0/1 | 6 |
| 2 | gsm8k | `20260912022315-35ce3edc` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 10 | 0/10 | 55 |
| 3 | gsm8k | `20260912022548-2dd2f6d7` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 50 | 0/50 | 72 |
| 4 | gsm8k | `20260912023746-1e898a10` | openai/Azure/gpt-4.1 | succeeded | 1.0 | 5 | 0/5 | 20 |
| 5 | gsm8k | `20260912022911-34b7362f` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 5 | 0/5 | 3 |
| 6 | gsm8k | `20260912023123-b2d2c487` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 5 | 0/5 | 6 |
| 7 | gsm8k | `20260912023341-00163bee` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 5 | 0/5 | 8 |
| 8 | gsm8k | `20260912023559-3efcbc9a` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 5 | 0/5 | 8 |
| 9 | tau2 | `20260912023944-f9d88e06` | openai/aws/claude-sonnet-5 | succeeded | 0.8 | 8 | 0/10 | 920 |
| 10 | tau2 | `20260912025729-661bcc93` | openai/aws/claude-sonnet-5 | succeeded | 0.8 | 16 | 0/20 | 458 |
| 11 | appworld | `20260912030707-01e73f11` | openai/gemini-2.5-pro | succeeded | 0.0 | 0 | 2/5 | 1530 |
| 12 | appworld | `20260912033437-1e953c07` | openai/gemini-2.5-pro | succeeded | 0.0 | 0 | 1/20 | 2075 |

**Token attribution:** complete — no row lost its usage-bearing span.

## 5. Contents of the manifest file

Every run writes a `manifest.json` at its S3 prefix — a self-describing index of the run's data objects, so a client discovers the whole run in one fetch with no S3 listing. It lists the 7 data artifacts (`run.json`, `report.ndjson`, `token_report.ndjson`, `span_report.ndjson`, `report.parquet`, `token_report.parquet`, `span_report.parquet`); the manifest does **not** list itself. Each entry carries `name`, `format`, `key`, public `url`, and `size_bytes`.

Example:

```json
{
  "run_id": "20260912022127-960a861d",
  "benchmark": "gsm8k",
  "prefix": "kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20260912022127-960a861d",
  "artifacts": [
    {
      "name": "run.json",
      "format": "json",
      "key": "kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20260912022127-960a861d/run.json",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20260912022127-960a861d/run.json",
      "size_bytes": 468
    },
    {
      "name": "report.ndjson",
      "format": "ndjson",
      "key": "kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20260912022127-960a861d/report.ndjson",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20260912022127-960a861d/report.ndjson",
      "size_bytes": 1025
    },
    {
      "name": "token_report.ndjson",
      "format": "ndjson",
      "key": "kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20260912022127-960a861d/token_report.ndjson",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20260912022127-960a861d/token_report.ndjson",
      "size_bytes": 370
    },
    {
      "name": "span_report.ndjson",
      "format": "ndjson",
      "key": "kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20260912022127-960a861d/span_report.ndjson",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20260912022127-960a861d/span_report.ndjson",
      "size_bytes": 53583
    },
    {
      "name": "report.parquet",
      "format": "parquet",
      "key": "kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20260912022127-960a861d/report.parquet",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20260912022127-960a861d/report.parquet",
      "size_bytes": 11984
    },
    {
      "name": "token_report.parquet",
      "format": "parquet",
      "key": "kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20260912022127-960a861d/token_report.parquet",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20260912022127-960a861d/token_report.parquet",
      "size_bytes": 4425
    },
    {
      "name": "span_report.parquet",
      "format": "parquet",
      "key": "kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20260912022127-960a861d/span_report.parquet",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20260912022127-960a861d/span_report.parquet",
      "size_bytes": 10889
    }
  ]
}
```


## 6. Per-run aggregate — input & output tokens

`median` then `mean` then `CV` for each direction. median is the robust centre, mean the arithmetic average, and a mean well above the median signals right-skew (a few long tasks). `CV` is population sigma / mean, i.e. how unevenly the token cost is spread; it shares its denominator with `mean`, not `median`. CV is `—` for single-task runs.

A `⚠` in the Lost column means some of that run's rows lost their usage-bearing span (§2), so **every token figure on that row is understated** — including the median/mean/CV, which are computed over all tasks and so are dragged down by the damaged ones. Do not quote them as the run's cost.

| # | Benchmark | Model | Tasks | Lost | LLM calls | Input | Output | Total | IN median | IN mean | IN CV | OUT median | OUT mean | OUT CV |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 1 | 0 | 2 | 320 | 87 | 407 | 320 | 320 | — | 87 | 87 | — |
| 2 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 10 | 0 | 20 | 3166 | 1831 | 4997 | 312 | 317 | 0.08 | 87 | 183 | 0.70 |
| 3 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 50 | 0 | 100 | 15684 | 9472 | 25156 | 310 | 314 | 0.06 | 120 | 189 | 0.77 |
| 4 | gsm8k | openai/Azure/gpt-4.1 | 5 | 0 | 20 | 4117 | 279 | 4396 | 815 | 823 | 0.45 | 54 | 56 | 0.40 |
| 5 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 5 | 0 | 10 | 1564 | 628 | 2192 | 306 | 313 | 0.09 | 87 | 126 | 0.61 |
| 6 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 5 | 0 | 10 | 1564 | 628 | 2192 | 306 | 313 | 0.09 | 87 | 126 | 0.61 |
| 7 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 5 | 0 | 10 | 1564 | 628 | 2192 | 306 | 313 | 0.09 | 87 | 126 | 0.89 |
| 8 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 5 | 0 | 10 | 1564 | 820 | 2384 | 306 | 313 | 0.09 | 87 | 164 | 0.76 |
| 9 | tau2 | openai/aws/claude-sonnet-5 | 10 | 0 | 119 | 948122 | 23711 | 971833 | 97934 | 94812 | 0.09 | 2350 | 2371 | 0.11 |
| 10 | tau2 | openai/aws/claude-sonnet-5 | 20 | 0 | 217 | 1561308 | 37237 | 1598545 | 82517 | 78065 | 0.25 | 1720 | 1862 | 0.33 |
| 11 | appworld | openai/gemini-2.5-pro | 4 | 0 | 86 | 659907 | 76836 | 736743 | 171212 | 164977 | 0.38 | 19540 | 19209 | 0.27 |
| 12 | appworld | openai/gemini-2.5-pro | 17 | 0 | 643 | 6444789 | 554097 | 6998886 | 388535 | 379105 | 0.64 | 40297 | 32594 | 0.55 |

## 7. Per-task detail

`model` is repeated on every row deliberately: token counts are only comparable within a model, and the runs do not all use the same one (#4 swaps the gsm8k model, so its ~815 input tokens per task are not comparable with the ~320 of #1–3/#5–8 on the same tasks).

`tool` is shown next to `llm` because their relationship is the tell for a damaged row: each tool call needs a preceding model turn, so `llm=1` beside `tool=11` cannot be real work — it is a lost span (§2), flagged `⚠`.

### Run #1 — gsm8k  ·  `20260912022127-960a861d`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 87 | 407 | 2 | 1 | True |  |

### Run #2 — gsm8k  ·  `20260912022315-35ce3edc`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 87 | 407 | 2 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 87 | 370 | 2 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 280 | 586 | 2 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 87 | 378 | 2 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 87 | 451 | 2 | 1 | True |  |
| 5 | openai/Azure/gpt-5-mini-2025-08-07 | 309 | 87 | 396 | 2 | 1 | True |  |
| 6 | openai/Azure/gpt-5-mini-2025-08-07 | 298 | 471 | 769 | 2 | 1 | True |  |
| 7 | openai/Azure/gpt-5-mini-2025-08-07 | 323 | 279 | 602 | 2 | 1 | True |  |
| 8 | openai/Azure/gpt-5-mini-2025-08-07 | 358 | 279 | 637 | 2 | 1 | True |  |
| 9 | openai/Azure/gpt-5-mini-2025-08-07 | 314 | 87 | 401 | 2 | 1 | True |  |

### Run #3 — gsm8k  ·  `20260912022548-2dd2f6d7`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 87 | 407 | 2 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 87 | 370 | 2 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 280 | 586 | 2 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 87 | 378 | 2 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 87 | 451 | 2 | 1 | True |  |
| 5 | openai/Azure/gpt-5-mini-2025-08-07 | 309 | 87 | 396 | 2 | 1 | True |  |
| 6 | openai/Azure/gpt-5-mini-2025-08-07 | 298 | 471 | 769 | 2 | 1 | True |  |
| 7 | openai/Azure/gpt-5-mini-2025-08-07 | 323 | 279 | 602 | 2 | 1 | True |  |
| 8 | openai/Azure/gpt-5-mini-2025-08-07 | 358 | 279 | 637 | 2 | 1 | True |  |
| 9 | openai/Azure/gpt-5-mini-2025-08-07 | 314 | 87 | 401 | 2 | 1 | True |  |
| 10 | openai/Azure/gpt-5-mini-2025-08-07 | 315 | 87 | 402 | 2 | 1 | True |  |
| 11 | openai/Azure/gpt-5-mini-2025-08-07 | 316 | 151 | 467 | 2 | 1 | True |  |
| 12 | openai/Azure/gpt-5-mini-2025-08-07 | 322 | 215 | 537 | 2 | 1 | True |  |
| 13 | openai/Azure/gpt-5-mini-2025-08-07 | 315 | 215 | 530 | 2 | 1 | True |  |
| 14 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 151 | 457 | 2 | 1 | True |  |
| 15 | openai/Azure/gpt-5-mini-2025-08-07 | 347 | 87 | 434 | 2 | 1 | True |  |
| 16 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 535 | 841 | 2 | 1 | True |  |
| 17 | openai/Azure/gpt-5-mini-2025-08-07 | 309 | 88 | 397 | 2 | 1 | True |  |
| 18 | openai/Azure/gpt-5-mini-2025-08-07 | 284 | 87 | 371 | 2 | 1 | True |  |
| 19 | openai/Azure/gpt-5-mini-2025-08-07 | 321 | 599 | 920 | 2 | 1 | True |  |
| 20 | openai/Azure/gpt-5-mini-2025-08-07 | 317 | 215 | 532 | 2 | 1 | True |  |
| 21 | openai/Azure/gpt-5-mini-2025-08-07 | 301 | 471 | 772 | 2 | 1 | True |  |
| 22 | openai/Azure/gpt-5-mini-2025-08-07 | 311 | 87 | 398 | 2 | 1 | True |  |
| 23 | openai/Azure/gpt-5-mini-2025-08-07 | 293 | 87 | 380 | 2 | 1 | True |  |
| 24 | openai/Azure/gpt-5-mini-2025-08-07 | 292 | 151 | 443 | 2 | 1 | True |  |
| 25 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 87 | 407 | 2 | 1 | True |  |
| 26 | openai/Azure/gpt-5-mini-2025-08-07 | 321 | 87 | 408 | 2 | 1 | True |  |
| 27 | openai/Azure/gpt-5-mini-2025-08-07 | 310 | 87 | 397 | 2 | 1 | True |  |
| 28 | openai/Azure/gpt-5-mini-2025-08-07 | 304 | 87 | 391 | 2 | 1 | True |  |
| 29 | openai/Azure/gpt-5-mini-2025-08-07 | 324 | 151 | 475 | 2 | 1 | True |  |
| 30 | openai/Azure/gpt-5-mini-2025-08-07 | 292 | 87 | 379 | 2 | 1 | True |  |
| 31 | openai/Azure/gpt-5-mini-2025-08-07 | 317 | 87 | 404 | 2 | 1 | True |  |
| 32 | openai/Azure/gpt-5-mini-2025-08-07 | 297 | 151 | 448 | 2 | 1 | True |  |
| 33 | openai/Azure/gpt-5-mini-2025-08-07 | 285 | 87 | 372 | 2 | 1 | True |  |
| 34 | openai/Azure/gpt-5-mini-2025-08-07 | 297 | 87 | 384 | 2 | 1 | True |  |
| 35 | openai/Azure/gpt-5-mini-2025-08-07 | 305 | 343 | 648 | 2 | 1 | True |  |
| 36 | openai/Azure/gpt-5-mini-2025-08-07 | 297 | 87 | 384 | 2 | 1 | True |  |
| 37 | openai/Azure/gpt-5-mini-2025-08-07 | 315 | 599 | 914 | 2 | 1 | True |  |
| 38 | openai/Azure/gpt-5-mini-2025-08-07 | 300 | 215 | 515 | 2 | 1 | True |  |
| 39 | openai/Azure/gpt-5-mini-2025-08-07 | 329 | 279 | 608 | 2 | 1 | True |  |
| 40 | openai/Azure/gpt-5-mini-2025-08-07 | 308 | 279 | 587 | 2 | 1 | True |  |
| 41 | openai/Azure/gpt-5-mini-2025-08-07 | 379 | 87 | 466 | 2 | 1 | True |  |
| 42 | openai/Azure/gpt-5-mini-2025-08-07 | 336 | 23 | 359 | 2 | 1 | True |  |
| 43 | openai/Azure/gpt-5-mini-2025-08-07 | 310 | 151 | 461 | 2 | 1 | True |  |
| 44 | openai/Azure/gpt-5-mini-2025-08-07 | 326 | 471 | 797 | 2 | 1 | True |  |
| 45 | openai/Azure/gpt-5-mini-2025-08-07 | 350 | 343 | 693 | 2 | 1 | True |  |
| 46 | openai/Azure/gpt-5-mini-2025-08-07 | 346 | 151 | 497 | 2 | 1 | True |  |
| 47 | openai/Azure/gpt-5-mini-2025-08-07 | 303 | 215 | 518 | 2 | 1 | True |  |
| 48 | openai/Azure/gpt-5-mini-2025-08-07 | 294 | 87 | 381 | 2 | 1 | True |  |
| 49 | openai/Azure/gpt-5-mini-2025-08-07 | 298 | 87 | 385 | 2 | 1 | True |  |

### Run #4 — gsm8k  ·  `20260912023746-1e898a10`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-4.1 | 815 | 54 | 869 | 4 | 3 | True |  |
| 1 | openai/Azure/gpt-4.1 | 446 | 37 | 483 | 3 | 2 | True |  |
| 2 | openai/Azure/gpt-4.1 | 1452 | 97 | 1549 | 6 | 5 | True |  |
| 3 | openai/Azure/gpt-4.1 | 459 | 36 | 495 | 3 | 2 | True |  |
| 4 | openai/Azure/gpt-4.1 | 945 | 55 | 1000 | 4 | 3 | True |  |

### Run #5 — gsm8k  ·  `20260912022911-34b7362f`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 87 | 407 | 2 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 87 | 370 | 2 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 280 | 586 | 2 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 87 | 378 | 2 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 87 | 451 | 2 | 1 | True |  |

### Run #6 — gsm8k  ·  `20260912023123-b2d2c487`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 87 | 407 | 2 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 87 | 370 | 2 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 280 | 586 | 2 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 87 | 378 | 2 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 87 | 451 | 2 | 1 | True |  |

### Run #7 — gsm8k  ·  `20260912023341-00163bee`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 87 | 407 | 2 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 23 | 306 | 2 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 344 | 650 | 2 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 87 | 378 | 2 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 87 | 451 | 2 | 1 | True |  |

### Run #8 — gsm8k  ·  `20260912023559-3efcbc9a`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 87 | 407 | 2 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 23 | 306 | 2 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 344 | 650 | 2 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 87 | 378 | 2 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 279 | 643 | 2 | 1 | True |  |

### Run #9 — tau2  ·  `20260912023944-f9d88e06`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/aws/claude-sonnet-5 | 80998 | 2108 | 83106 | 12 | 11 | True |  |
| 1 | openai/aws/claude-sonnet-5 | 99274 | 2482 | 101756 | 13 | 13 | False |  |
| 2 | openai/aws/claude-sonnet-5 | 78772 | 2006 | 80778 | 10 | 10 | True |  |
| 3 | openai/aws/claude-sonnet-5 | 96594 | 2177 | 98771 | 12 | 12 | True |  |
| 4 | openai/aws/claude-sonnet-5 | 100868 | 2219 | 103087 | 12 | 12 | False |  |
| 5 | openai/aws/claude-sonnet-5 | 100114 | 2771 | 102885 | 12 | 12 | True |  |
| 6 | openai/aws/claude-sonnet-5 | 107733 | 2482 | 110215 | 13 | 13 | True |  |
| 7 | openai/aws/claude-sonnet-5 | 100429 | 2146 | 102575 | 12 | 12 | True |  |
| 8 | openai/aws/claude-sonnet-5 | 94976 | 2796 | 97772 | 12 | 12 | True |  |
| 9 | openai/aws/claude-sonnet-5 | 88364 | 2524 | 90888 | 11 | 11 | True |  |

### Run #10 — tau2  ·  `20260912025729-661bcc93`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/aws/claude-sonnet-5 | 81675 | 1620 | 83295 | 12 | 11 | True |  |
| 1 | openai/aws/claude-sonnet-5 | 87602 | 1800 | 89402 | 12 | 12 | True |  |
| 2 | openai/aws/claude-sonnet-5 | 88035 | 1428 | 89463 | 11 | 11 | True |  |
| 3 | openai/aws/claude-sonnet-5 | 70745 | 1727 | 72472 | 9 | 9 | False |  |
| 4 | openai/aws/claude-sonnet-5 | 97064 | 2293 | 99357 | 12 | 12 | True |  |
| 5 | openai/aws/claude-sonnet-5 | 107142 | 2711 | 109853 | 13 | 13 | False |  |
| 6 | openai/aws/claude-sonnet-5 | 76210 | 2194 | 78404 | 10 | 10 | True |  |
| 7 | openai/aws/claude-sonnet-5 | 95084 | 2718 | 97802 | 12 | 12 | True |  |
| 8 | openai/aws/claude-sonnet-5 | 89055 | 2617 | 91672 | 11 | 11 | True |  |
| 9 | openai/aws/claude-sonnet-5 | 102514 | 2744 | 105258 | 12 | 12 | True |  |
| 10 | openai/aws/claude-sonnet-5 | 26750 | 862 | 27612 | 6 | 6 | True |  |
| 11 | openai/aws/claude-sonnet-5 | 58262 | 1307 | 59569 | 10 | 10 | True |  |
| 12 | openai/aws/claude-sonnet-5 | 72223 | 1599 | 73822 | 12 | 12 | True |  |
| 13 | openai/aws/claude-sonnet-5 | 83359 | 1690 | 85049 | 13 | 13 | True |  |
| 14 | openai/aws/claude-sonnet-5 | 62454 | 1314 | 63768 | 10 | 10 | True |  |
| 15 | openai/aws/claude-sonnet-5 | 90624 | 1713 | 92337 | 12 | 12 | False |  |
| 16 | openai/aws/claude-sonnet-5 | 73103 | 2316 | 75419 | 10 | 10 | True |  |
| 17 | openai/aws/claude-sonnet-5 | 43346 | 638 | 43984 | 8 | 8 | True |  |
| 18 | openai/aws/claude-sonnet-5 | 92472 | 1452 | 93924 | 14 | 14 | False |  |
| 19 | openai/aws/claude-sonnet-5 | 63589 | 2494 | 66083 | 8 | 10 | True |  |

### Run #11 — appworld  ·  `20260912030707-01e73f11`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 3d9a636_1 | openai/gemini-2.5-pro | 123992 | 16306 | 140298 | 17 | 8 | False |  |
| 3d9a636_2 | openai/gemini-2.5-pro | 218433 | 25442 | 243875 | 29 | 14 | None |  |
| 3d9a636_3 | openai/gemini-2.5-pro | 232652 | 22773 | 255425 | 27 | 13 | False |  |
| fd1f8fa_1 | openai/gemini-2.5-pro | 84830 | 12315 | 97145 | 13 | 6 | None |  |

### Run #12 — appworld  ·  `20260912033437-1e953c07`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 3d9a636_1 | openai/gemini-2.5-pro | 145273 | 16066 | 161339 | 19 | 9 | False |  |
| 3d9a636_2 | openai/gemini-2.5-pro | 128411 | 12898 | 141309 | 17 | 8 | False |  |
| 3d9a636_3 | openai/gemini-2.5-pro | 805175 | 50575 | 855750 | 71 | 35 | False |  |
| 21abae1_1 | openai/gemini-2.5-pro | 64460 | 8178 | 72638 | 11 | 5 | False |  |
| 21abae1_2 | openai/gemini-2.5-pro | 65702 | 8745 | 74447 | 11 | 5 | False |  |
| 21abae1_3 | openai/gemini-2.5-pro | 88268 | 11641 | 99909 | 15 | 7 | False |  |
| 29a7b7e_2 | openai/gemini-2.5-pro | 576616 | 58544 | 635160 | 57 | 28 | False |  |
| 29a7b7e_3 | openai/gemini-2.5-pro | 388535 | 41412 | 429947 | 41 | 20 | None |  |
| 325d6ec_1 | openai/gemini-2.5-pro | 250001 | 21963 | 271964 | 35 | 17 | False |  |
| 325d6ec_2 | openai/gemini-2.5-pro | 138343 | 14680 | 153023 | 21 | 10 | False |  |
| 325d6ec_3 | openai/gemini-2.5-pro | 279873 | 26104 | 305977 | 37 | 18 | False |  |
| 634f342_1 | openai/gemini-2.5-pro | 557943 | 48540 | 606483 | 61 | 30 | False |  |
| 8749218_1 | openai/gemini-2.5-pro | 646850 | 40297 | 687147 | 41 | 20 | False |  |
| 8749218_2 | openai/gemini-2.5-pro | 602982 | 43434 | 646416 | 51 | 25 | False |  |
| fd1f8fa_1 | openai/gemini-2.5-pro | 461059 | 42606 | 503665 | 45 | 22 | False |  |
| fd1f8fa_2 | openai/gemini-2.5-pro | 674399 | 60684 | 735083 | 57 | 28 | False |  |
| fd1f8fa_3 | openai/gemini-2.5-pro | 570899 | 47730 | 618629 | 53 | 26 | False |  |


## 8. Per-task span inventory

Which spans each task actually invoked, by name. §6 and §7 report *counts*; this is what they were counted from, read straight out of each run's `span_report.ndjson`.

Read the **chat** column first. Every task issues a `max_tokens=1` capability probe plus its real model calls, so a healthy task shows **at least 2** chat spans. Exactly **1** means the usage-bearing span for the real call was never written — the warm-agent defect — and that is visible here as a span *count*, independent of any token value (which is what makes it catchable on claude-sonnet-5 and gemini-2.5-pro, where the surviving probe reports a non-zero 8/1 or 1/0).

`not counted` are spans the aggregator cannot see: it only folds a chat/tool span into `llm_count`/`tool_count` when its parent is the `invoke_agent` span, so anything nested deeper is real work missing from the totals. A non-zero figure there is not a bug by itself — it is the known blind spot, now measurable.

The **names** column lists only harness-named spans (`root`/`phase`/`agent`/`chat`/`tool`). The `other` column counts the rest — A2A/HTTP framework internals such as `EventQueue.dequeue_event`, which dominate the raw count (a single gsm8k task emits ~98 spans, ~90 of them framework noise) and would swamp this table. They are all present in `span_report.ndjson`; this section is the readable summary, not the full tree.

### Run #1 — gsm8k  ·  `20260912022127-960a861d`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 102 | 2 | 2 | 93 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #2 — gsm8k  ·  `20260912022315-35ce3edc`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 98 | 2 | 2 | 89 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 95 | 2 | 2 | 86 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 100 | 2 | 2 | 91 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 111 | 2 | 2 | 102 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 95 | 2 | 2 | 86 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 5 | 110 | 2 | 2 | 101 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 6 | 101 | 2 | 2 | 92 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 7 | 98 | 2 | 2 | 89 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 8 | 104 | 2 | 2 | 95 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 9 | 96 | 2 | 2 | 87 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #3 — gsm8k  ·  `20260912022548-2dd2f6d7`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 105 | 2 | 2 | 96 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 106 | 2 | 2 | 97 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 107 | 2 | 2 | 98 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 94 | 2 | 2 | 85 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 92 | 2 | 2 | 83 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 5 | 91 | 2 | 2 | 82 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 6 | 91 | 2 | 2 | 82 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 7 | 92 | 2 | 2 | 83 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 8 | 92 | 2 | 2 | 83 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 9 | 92 | 2 | 2 | 83 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 10 | 95 | 2 | 2 | 86 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 11 | 97 | 2 | 2 | 88 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 12 | 96 | 2 | 2 | 87 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 13 | 99 | 2 | 2 | 90 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 14 | 96 | 2 | 2 | 87 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 15 | 98 | 2 | 2 | 89 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 16 | 101 | 2 | 2 | 92 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 17 | 95 | 2 | 2 | 86 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 18 | 95 | 2 | 2 | 86 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 19 | 101 | 2 | 2 | 92 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 20 | 97 | 2 | 2 | 88 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 21 | 99 | 2 | 2 | 90 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 22 | 95 | 2 | 2 | 86 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 23 | 96 | 2 | 2 | 87 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 24 | 97 | 2 | 2 | 88 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 25 | 95 | 2 | 2 | 86 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 26 | 96 | 2 | 2 | 87 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 27 | 96 | 2 | 2 | 87 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 28 | 106 | 2 | 2 | 97 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 29 | 106 | 2 | 2 | 97 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 30 | 121 | 2 | 2 | 112 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 31 | 121 | 2 | 2 | 112 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 32 | 109 | 2 | 2 | 100 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 33 | 108 | 2 | 2 | 99 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 34 | 94 | 2 | 2 | 85 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 35 | 100 | 2 | 2 | 91 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 36 | 99 | 2 | 2 | 90 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 37 | 109 | 2 | 2 | 100 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 38 | 104 | 2 | 2 | 95 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 39 | 101 | 2 | 2 | 92 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 40 | 100 | 2 | 2 | 91 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 41 | 114 | 2 | 2 | 105 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 42 | 94 | 2 | 2 | 85 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 43 | 97 | 2 | 2 | 88 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 44 | 108 | 2 | 2 | 99 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 45 | 107 | 2 | 2 | 98 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 46 | 103 | 2 | 2 | 94 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 47 | 111 | 2 | 2 | 102 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 48 | 108 | 2 | 2 | 99 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 49 | 109 | 2 | 2 | 100 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #4 — gsm8k  ·  `20260912023746-1e898a10`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 127 | 4 | 4 | 114 | 1 | `chat Azure/gpt-4.1` x4, `execute_tool calculate_expression` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 114 | 3 | 3 | 103 | 1 | `chat Azure/gpt-4.1` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool calculate_expression`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 152 | 6 | 6 | 135 | 1 | `chat Azure/gpt-4.1` x6, `execute_tool calculate_expression` x4, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 141 | 3 | 3 | 130 | 1 | `chat Azure/gpt-4.1` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool calculate_expression`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 133 | 4 | 4 | 120 | 1 | `chat Azure/gpt-4.1` x4, `execute_tool calculate_expression` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #5 — gsm8k  ·  `20260912022911-34b7362f`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 96 | 2 | 2 | 87 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 94 | 2 | 2 | 85 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 93 | 2 | 2 | 84 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 94 | 2 | 2 | 85 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 92 | 2 | 2 | 83 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #6 — gsm8k  ·  `20260912023123-b2d2c487`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 96 | 2 | 2 | 87 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 100 | 2 | 2 | 91 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 96 | 2 | 2 | 87 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 95 | 2 | 2 | 86 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 95 | 2 | 2 | 86 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #7 — gsm8k  ·  `20260912023341-00163bee`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 103 | 2 | 2 | 94 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 100 | 2 | 2 | 91 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 104 | 2 | 2 | 95 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 103 | 2 | 2 | 94 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 96 | 2 | 2 | 87 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #8 — gsm8k  ·  `20260912023559-3efcbc9a`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 98 | 2 | 2 | 89 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 100 | 2 | 2 | 91 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 106 | 2 | 2 | 97 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 101 | 2 | 2 | 92 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 98 | 2 | 2 | 89 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #9 — tau2  ·  `20260912023944-f9d88e06`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 361 | 12 | 12 | 332 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x5, `execute_tool get_order_details` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 1 | 388 | 13 | 14 | 356 | 1 | `chat aws/claude-sonnet-5` x13, `execute_tool message` x6, `execute_tool get_order_details` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `execute_tool transfer_to_human_agents`, `Evaluator.Evaluate` |
| 2 | 357 | 10 | 11 | 331 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 3 | 470 | 12 | 13 | 440 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 4 | 399 | 12 | 13 | 369 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool get_order_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 5 | 372 | 12 | 13 | 342 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 6 | 443 | 13 | 14 | 411 | 1 | `chat aws/claude-sonnet-5` x13, `execute_tool message` x8, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 7 | 370 | 12 | 13 | 340 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 8 | 375 | 12 | 13 | 345 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 9 | 402 | 11 | 12 | 374 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |

### Run #10 — tau2  ·  `20260912025729-661bcc93`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 364 | 12 | 12 | 335 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x5, `execute_tool get_order_details` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 1 | 400 | 12 | 13 | 370 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x6, `execute_tool get_order_details` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 2 | 386 | 11 | 12 | 358 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 3 | 327 | 9 | 10 | 303 | 1 | `chat aws/claude-sonnet-5` x9, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 4 | 413 | 12 | 13 | 383 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 5 | 430 | 13 | 14 | 398 | 1 | `chat aws/claude-sonnet-5` x13, `execute_tool message` x8, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 6 | 364 | 10 | 11 | 338 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 7 | 404 | 12 | 13 | 374 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 8 | 393 | 11 | 12 | 365 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 9 | 435 | 12 | 13 | 405 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 10 | 229 | 6 | 7 | 211 | 1 | `chat aws/claude-sonnet-5` x6, `execute_tool message` x4, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool transfer_to_human_agents`, `Evaluator.Evaluate` |
| 11 | 391 | 10 | 11 | 365 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 12 | 379 | 12 | 13 | 349 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x8, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool transfer_to_human_agents`, `Evaluator.Evaluate` |
| 13 | 402 | 13 | 14 | 370 | 1 | `chat aws/claude-sonnet-5` x13, `execute_tool message` x8, `execute_tool return_delivered_order_items` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `Evaluator.Evaluate` |
| 14 | 333 | 10 | 11 | 307 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 15 | 389 | 12 | 13 | 359 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 16 | 384 | 10 | 11 | 358 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool cancel_pending_order`, `Evaluator.Evaluate` |
| 17 | 274 | 8 | 9 | 252 | 1 | `chat aws/claude-sonnet-5` x8, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_order_details`, `execute_tool modify_pending_order_address`, `Evaluator.Evaluate` |
| 18 | 409 | 14 | 15 | 375 | 1 | `chat aws/claude-sonnet-5` x14, `execute_tool message` x10, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 19 | 334 | 8 | 11 | 310 | 1 | `chat aws/claude-sonnet-5` x8, `execute_tool message` x4, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool return_delivered_order_items`, `execute_tool transfer_to_human_agents`, `Evaluator.Evaluate` |

### Run #11 — appworld  ·  `20260912030707-01e73f11`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 3d9a636_1 | 635 | 17 | 9 | 604 | 1 | `chat gemini-2.5-pro` x17, `execute_tool phone__search_contacts` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool phone__login`, `execute_tool phone__show_contact_relationships`, `execute_tool venmo__add_friend`, `execute_tool venmo__remove_friend`, `execute_tool finish`, `Evaluator.Evaluate` |
| 3d9a636_2 | 896 | 29 | 15 | 848 | 1 | `chat gemini-2.5-pro` x29, `execute_tool phone__login` x3, `execute_tool phone__search_contacts` x3, `execute_tool phone__show_contact_relationships` x2, `execute_tool venmo__search_friends` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool supervisor__show_profile`, `execute_tool venmo__add_friend`, `execute_tool venmo__remove_friend` |
| 3d9a636_3 | 831 | 27 | 14 | 785 | 1 | `chat gemini-2.5-pro` x27, `execute_tool phone__search_contacts` x4, `execute_tool phone__login` x2, `execute_tool venmo__search_friends` x2, `execute_tool venmo__remove_friend` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__add_friend`, `execute_tool finish`, `Evaluator.Evaluate` |
| fd1f8fa_1 | 468 | 13 | 7 | 444 | 1 | `chat gemini-2.5-pro` x13, `execute_tool spotify__show_liked_songs` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool spotify__show_song_queue`, `execute_tool spotify__remove_song_from_queue` |

### Run #12 — appworld  ·  `20260912033437-1e953c07`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 3d9a636_1 | 590 | 19 | 10 | 556 | 1 | `chat gemini-2.5-pro` x19, `execute_tool phone__login` x2, `execute_tool phone__search_contacts` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool phone__show_contact_relationships`, `execute_tool venmo__login`, `execute_tool venmo__remove_friend`, `execute_tool finish`, `Evaluator.Evaluate` |
| 3d9a636_2 | 506 | 17 | 9 | 475 | 1 | `chat gemini-2.5-pro` x17, `execute_tool phone__search_contacts` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool phone__login`, `execute_tool venmo__add_friend`, `execute_tool venmo__remove_friend`, `execute_tool venmo__search_friends`, `execute_tool finish`, `Evaluator.Evaluate` |
| 3d9a636_3 | 1594 | 71 | 36 | 1482 | 1 | `chat gemini-2.5-pro` x71, `execute_tool phone__search_contacts` x11, `execute_tool venmo__add_friend` x10, `execute_tool venmo__remove_friend` x4, `execute_tool phone__login` x3, `execute_tool venmo__login` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool phone__show_contact_relationships`, `execute_tool venmo__search_friends`, `execute_tool finish`, `Evaluator.Evaluate` |
| 21abae1_1 | 321 | 11 | 6 | 299 | 1 | `chat gemini-2.5-pro` x11, `execute_tool venmo__show_transactions` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| 21abae1_2 | 333 | 11 | 6 | 311 | 1 | `chat gemini-2.5-pro` x11, `execute_tool venmo__show_transactions` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| 21abae1_3 | 417 | 15 | 8 | 389 | 1 | `chat gemini-2.5-pro` x15, `execute_tool venmo__show_transactions` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool phone__get_current_date_and_time`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| 29a7b7e_2 | 1644 | 57 | 29 | 1553 | 1 | `chat gemini-2.5-pro` x57, `execute_tool file_system__move_file` x20, `execute_tool file_system__create_directory` x2, `execute_tool file_system__delete_directory` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool file_system__login`, `execute_tool file_system__show_directory`, `execute_tool finish`, `Evaluator.Evaluate` |
| 29a7b7e_3 | 1137 | 41 | 21 | 1071 | 1 | `chat gemini-2.5-pro` x41, `execute_tool file_system__move_file` x13, `execute_tool file_system__show_directory` x2, `execute_tool file_system__create_directory` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool file_system__login`, `execute_tool file_system__move__file` |
| 325d6ec_1 | 758 | 35 | 18 | 700 | 1 | `chat gemini-2.5-pro` x35, `execute_tool spotify__show_song_privates` x6, `execute_tool spotify__previous_song` x5, `execute_tool spotify__show_current_song` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool spotify__show_song`, `execute_tool finish`, `Evaluator.Evaluate` |
| 325d6ec_2 | 519 | 21 | 11 | 482 | 1 | `chat gemini-2.5-pro` x21, `execute_tool spotify__next_song` x4, `execute_tool spotify__login` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__show_downloaded_songs`, `execute_tool spotify__show_current_song`, `execute_tool finish`, `Evaluator.Evaluate` |
| 325d6ec_3 | 861 | 37 | 19 | 800 | 1 | `chat gemini-2.5-pro` x37, `execute_tool spotify__show_current_song` x6, `execute_tool spotify__show_song_privates` x5, `execute_tool spotify__next_song` x4, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| 634f342_1 | 1473 | 61 | 31 | 1376 | 1 | `chat gemini-2.5-pro` x61, `execute_tool spotify__show_song` x12, `execute_tool spotify__remove_song_from_playlist` x5, `execute_tool spotify__add_song_to_playlist` x5, `execute_tool file_system__login` x2, `execute_tool spotify__show_playlist_library` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool file_system__show_file`, `execute_tool spotify__create_playlist`, `execute_tool finish`, `Evaluator.Evaluate` |
| 8749218_1 | 1362 | 41 | 21 | 1295 | 1 | `chat gemini-2.5-pro` x41, `execute_tool spotify__show_recommendations` x4, `execute_tool spotify__add_to_queue` x4, `execute_tool spotify__clear_song_queue` x2, `execute_tool spotify__add_song_to_playlist` x2, `execute_tool spotify__play_music` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool spotify__create_playlist`, `execute_tool spotify__show_playlist`, `execute_tool phone__show_text_message`, `execute_tool finish`, `Evaluator.Evaluate` |
| 8749218_2 | 1426 | 51 | 26 | 1344 | 1 | `chat gemini-2.5-pro` x51, `execute_tool spotify__show_recommendations` x7, `execute_tool spotify__add_song_to_playlist` x6, `execute_tool spotify__add_to_queue` x4, `execute_tool spotify__clear_song_queue` x2, `execute_tool spotify__play_music` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool spotify__create_playlist`, `execute_tool finish`, `Evaluator.Evaluate` |
| fd1f8fa_1 | 1189 | 45 | 23 | 1116 | 1 | `chat gemini-2.5-pro` x45, `execute_tool spotify__remove_song_from_queue` x11, `execute_tool spotify__play_music` x4, `execute_tool spotify__show_liked_songs` x2, `execute_tool spotify__show_song_queue` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| fd1f8fa_2 | 1596 | 57 | 29 | 1505 | 1 | `chat gemini-2.5-pro` x57, `execute_tool spotify__remove_song_from_queue` x12, `execute_tool spotify__play_music` x7, `execute_tool spotify__show_liked_songs` x4, `execute_tool spotify__show_song_queue` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| fd1f8fa_3 | 1354 | 53 | 27 | 1269 | 1 | `chat gemini-2.5-pro` x53, `execute_tool spotify__remove_song_from_queue` x11, `execute_tool spotify__show_liked_songs` x4, `execute_tool spotify__play_music` x4, `execute_tool spotify__show_song_queue` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool spotify__next_song`, `execute_tool spotify__show_current_song`, `execute_tool finish`, `Evaluator.Evaluate` |

