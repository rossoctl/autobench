# AutoBench Service — 12 Parameterized Runs (OpenShift — ykt3 Service / ykt2 workloads)

**Report generated:** 2026-09-12T19:09:48Z  
**Service version:** `v1.27`  
**Platform:** OpenShift — ykt3 Service / ykt2 workloads  
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
| **Key prefix (all 96 objects)** | `ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/` |

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
| 1 | gsm8k | `20260912000728-c0d99c5a` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 1 | 0/1 | 14 |
| 2 | gsm8k | `20260912000921-e6358837` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 10 | 0/10 | 60 |
| 3 | gsm8k | `20260912001200-309fc2b2` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 50 | 0/50 | 166 |
| 4 | gsm8k | `20260912002822-3f3e2a21` | openai/Azure/gpt-4.1 | succeeded | 0.8 | 4 | 0/5 | 13 |
| 5 | gsm8k | `20260912001716-258c1474` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 5 | 0/5 | 29 |
| 6 | gsm8k | `20260912002010-2faa67bc` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 5 | 0/5 | 31 |
| 7 | gsm8k | `20260912002309-1d8fea67` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 5 | 0/5 | 25 |
| 8 | gsm8k | `20260912002558-11de2ab5` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 5 | 0/5 | 29 |
| 9 | tau2 | `20260912003101-52b759c7` | openai/aws/claude-sonnet-5 | succeeded | 0.9 | 9 | 0/10 | 1184 |
| 10 | tau2 | `20260912005325-526994f6` | openai/aws/claude-sonnet-5 | succeeded | 0.8 | 16 | 0/20 | 725 |
| 11 | appworld | `20260912010713-8a93e497` | openai/gemini-2.5-pro | succeeded | 0.0 | 0 | 0/5 | 2211 |
| 12 | appworld | `20260912014605-2c6c751c` | openai/gemini-2.5-pro | succeeded | 0.0 | 0 | 2/20 | 1887 |

**Token attribution:** complete — no row lost its usage-bearing span.

## 5. Contents of the manifest file

Every run writes a `manifest.json` at its S3 prefix — a self-describing index of the run's data objects, so a client discovers the whole run in one fetch with no S3 listing. It lists the 7 data artifacts (`run.json`, `report.ndjson`, `token_report.ndjson`, `span_report.ndjson`, `report.parquet`, `token_report.parquet`, `span_report.parquet`); the manifest does **not** list itself. Each entry carries `name`, `format`, `key`, public `url`, and `size_bytes`.

Example:

```json
{
  "run_id": "20260912000728-c0d99c5a",
  "benchmark": "gsm8k",
  "prefix": "ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/gsm8k/20260912000728-c0d99c5a",
  "artifacts": [
    {
      "name": "run.json",
      "format": "json",
      "key": "ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/gsm8k/20260912000728-c0d99c5a/run.json",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/gsm8k/20260912000728-c0d99c5a/run.json",
      "size_bytes": 470
    },
    {
      "name": "report.ndjson",
      "format": "ndjson",
      "key": "ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/gsm8k/20260912000728-c0d99c5a/report.ndjson",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/gsm8k/20260912000728-c0d99c5a/report.ndjson",
      "size_bytes": 1048
    },
    {
      "name": "token_report.ndjson",
      "format": "ndjson",
      "key": "ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/gsm8k/20260912000728-c0d99c5a/token_report.ndjson",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/gsm8k/20260912000728-c0d99c5a/token_report.ndjson",
      "size_bytes": 370
    },
    {
      "name": "span_report.ndjson",
      "format": "ndjson",
      "key": "ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/gsm8k/20260912000728-c0d99c5a/span_report.ndjson",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/gsm8k/20260912000728-c0d99c5a/span_report.ndjson",
      "size_bytes": 61795
    },
    {
      "name": "report.parquet",
      "format": "parquet",
      "key": "ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/gsm8k/20260912000728-c0d99c5a/report.parquet",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/gsm8k/20260912000728-c0d99c5a/report.parquet",
      "size_bytes": 11984
    },
    {
      "name": "token_report.parquet",
      "format": "parquet",
      "key": "ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/gsm8k/20260912000728-c0d99c5a/token_report.parquet",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/gsm8k/20260912000728-c0d99c5a/token_report.parquet",
      "size_bytes": 4425
    },
    {
      "name": "span_report.parquet",
      "format": "parquet",
      "key": "ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/gsm8k/20260912000728-c0d99c5a/span_report.parquet",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/gsm8k/20260912000728-c0d99c5a/span_report.parquet",
      "size_bytes": 11524
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
| 2 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 10 | 0 | 20 | 3166 | 2151 | 5317 | 312 | 317 | 0.08 | 151 | 215 | 0.96 |
| 3 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 50 | 0 | 100 | 15684 | 9728 | 25412 | 310 | 314 | 0.06 | 151 | 195 | 0.82 |
| 4 | gsm8k | openai/Azure/gpt-4.1 | 5 | 0 | 19 | 3927 | 323 | 4250 | 815 | 785 | 0.37 | 54 | 65 | 0.40 |
| 5 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 5 | 0 | 10 | 1564 | 692 | 2256 | 306 | 313 | 0.09 | 87 | 138 | 0.74 |
| 6 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 5 | 0 | 10 | 1564 | 692 | 2256 | 306 | 313 | 0.09 | 87 | 138 | 0.54 |
| 7 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 5 | 0 | 10 | 1564 | 692 | 2256 | 306 | 313 | 0.09 | 87 | 138 | 0.54 |
| 8 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 5 | 0 | 10 | 1564 | 692 | 2256 | 306 | 313 | 0.09 | 87 | 138 | 0.54 |
| 9 | tau2 | openai/aws/claude-sonnet-5 | 10 | 0 | 110 | 854508 | 21756 | 876264 | 87870 | 85451 | 0.11 | 2230 | 2176 | 0.15 |
| 10 | tau2 | openai/aws/claude-sonnet-5 | 19 | 0 | 203 | 1484987 | 35630 | 1520617 | 79958 | 78157 | 0.21 | 1920 | 1875 | 0.26 |
| 11 | appworld | openai/gemini-2.5-pro | 5 | 0 | 177 | 1897947 | 169016 | 2066963 | 398663 | 379589 | 0.26 | 34569 | 33803 | 0.16 |
| 12 | appworld | openai/gemini-2.5-pro | 18 | 0 | 542 | 5162895 | 471370 | 5634265 | 211040 | 286828 | 0.65 | 25065 | 26187 | 0.49 |

## 7. Per-task detail

`model` is repeated on every row deliberately: token counts are only comparable within a model, and the runs do not all use the same one (#4 swaps the gsm8k model, so its ~815 input tokens per task are not comparable with the ~320 of #1–3/#5–8 on the same tasks).

`tool` is shown next to `llm` because their relationship is the tell for a damaged row: each tool call needs a preceding model turn, so `llm=1` beside `tool=11` cannot be real work — it is a lost span (§2), flagged `⚠`.

### Run #1 — gsm8k  ·  `20260912000728-c0d99c5a`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 87 | 407 | 2 | 1 | True |  |

### Run #2 — gsm8k  ·  `20260912000921-e6358837`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 87 | 407 | 2 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 87 | 370 | 2 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 344 | 650 | 2 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 87 | 378 | 2 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 87 | 451 | 2 | 1 | True |  |
| 5 | openai/Azure/gpt-5-mini-2025-08-07 | 309 | 151 | 460 | 2 | 1 | True |  |
| 6 | openai/Azure/gpt-5-mini-2025-08-07 | 298 | 151 | 449 | 2 | 1 | True |  |
| 7 | openai/Azure/gpt-5-mini-2025-08-07 | 323 | 791 | 1114 | 2 | 1 | True |  |
| 8 | openai/Azure/gpt-5-mini-2025-08-07 | 358 | 215 | 573 | 2 | 1 | True |  |
| 9 | openai/Azure/gpt-5-mini-2025-08-07 | 314 | 151 | 465 | 2 | 1 | True |  |

### Run #3 — gsm8k  ·  `20260912001200-309fc2b2`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 87 | 407 | 2 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 87 | 370 | 2 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 344 | 650 | 2 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 87 | 378 | 2 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 87 | 451 | 2 | 1 | True |  |
| 5 | openai/Azure/gpt-5-mini-2025-08-07 | 309 | 151 | 460 | 2 | 1 | True |  |
| 6 | openai/Azure/gpt-5-mini-2025-08-07 | 298 | 151 | 449 | 2 | 1 | True |  |
| 7 | openai/Azure/gpt-5-mini-2025-08-07 | 323 | 791 | 1114 | 2 | 1 | True |  |
| 8 | openai/Azure/gpt-5-mini-2025-08-07 | 358 | 215 | 573 | 2 | 1 | True |  |
| 9 | openai/Azure/gpt-5-mini-2025-08-07 | 314 | 151 | 465 | 2 | 1 | True |  |
| 10 | openai/Azure/gpt-5-mini-2025-08-07 | 315 | 87 | 402 | 2 | 1 | True |  |
| 11 | openai/Azure/gpt-5-mini-2025-08-07 | 316 | 87 | 403 | 2 | 1 | True |  |
| 12 | openai/Azure/gpt-5-mini-2025-08-07 | 322 | 663 | 985 | 2 | 1 | True |  |
| 13 | openai/Azure/gpt-5-mini-2025-08-07 | 315 | 279 | 594 | 2 | 1 | True |  |
| 14 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 151 | 457 | 2 | 1 | True |  |
| 15 | openai/Azure/gpt-5-mini-2025-08-07 | 347 | 215 | 562 | 2 | 1 | True |  |
| 16 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 151 | 457 | 2 | 1 | True |  |
| 17 | openai/Azure/gpt-5-mini-2025-08-07 | 309 | 152 | 461 | 2 | 1 | True |  |
| 18 | openai/Azure/gpt-5-mini-2025-08-07 | 284 | 87 | 371 | 2 | 1 | True |  |
| 19 | openai/Azure/gpt-5-mini-2025-08-07 | 321 | 215 | 536 | 2 | 1 | True |  |
| 20 | openai/Azure/gpt-5-mini-2025-08-07 | 317 | 279 | 596 | 2 | 1 | True |  |
| 21 | openai/Azure/gpt-5-mini-2025-08-07 | 301 | 87 | 388 | 2 | 1 | True |  |
| 22 | openai/Azure/gpt-5-mini-2025-08-07 | 311 | 87 | 398 | 2 | 1 | True |  |
| 23 | openai/Azure/gpt-5-mini-2025-08-07 | 293 | 87 | 380 | 2 | 1 | True |  |
| 24 | openai/Azure/gpt-5-mini-2025-08-07 | 292 | 87 | 379 | 2 | 1 | True |  |
| 25 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 87 | 407 | 2 | 1 | True |  |
| 26 | openai/Azure/gpt-5-mini-2025-08-07 | 321 | 87 | 408 | 2 | 1 | True |  |
| 27 | openai/Azure/gpt-5-mini-2025-08-07 | 310 | 87 | 397 | 2 | 1 | True |  |
| 28 | openai/Azure/gpt-5-mini-2025-08-07 | 304 | 87 | 391 | 2 | 1 | True |  |
| 29 | openai/Azure/gpt-5-mini-2025-08-07 | 324 | 87 | 411 | 2 | 1 | True |  |
| 30 | openai/Azure/gpt-5-mini-2025-08-07 | 292 | 87 | 379 | 2 | 1 | True |  |
| 31 | openai/Azure/gpt-5-mini-2025-08-07 | 317 | 151 | 468 | 2 | 1 | True |  |
| 32 | openai/Azure/gpt-5-mini-2025-08-07 | 297 | 535 | 832 | 2 | 1 | True |  |
| 33 | openai/Azure/gpt-5-mini-2025-08-07 | 285 | 87 | 372 | 2 | 1 | True |  |
| 34 | openai/Azure/gpt-5-mini-2025-08-07 | 297 | 535 | 832 | 2 | 1 | True |  |
| 35 | openai/Azure/gpt-5-mini-2025-08-07 | 305 | 87 | 392 | 2 | 1 | True |  |
| 36 | openai/Azure/gpt-5-mini-2025-08-07 | 297 | 87 | 384 | 2 | 1 | True |  |
| 37 | openai/Azure/gpt-5-mini-2025-08-07 | 315 | 471 | 786 | 2 | 1 | True |  |
| 38 | openai/Azure/gpt-5-mini-2025-08-07 | 300 | 215 | 515 | 2 | 1 | True |  |
| 39 | openai/Azure/gpt-5-mini-2025-08-07 | 329 | 215 | 544 | 2 | 1 | True |  |
| 40 | openai/Azure/gpt-5-mini-2025-08-07 | 308 | 279 | 587 | 2 | 1 | True |  |
| 41 | openai/Azure/gpt-5-mini-2025-08-07 | 379 | 151 | 530 | 2 | 1 | True |  |
| 42 | openai/Azure/gpt-5-mini-2025-08-07 | 336 | 23 | 359 | 2 | 1 | True |  |
| 43 | openai/Azure/gpt-5-mini-2025-08-07 | 310 | 215 | 525 | 2 | 1 | True |  |
| 44 | openai/Azure/gpt-5-mini-2025-08-07 | 326 | 407 | 733 | 2 | 1 | True |  |
| 45 | openai/Azure/gpt-5-mini-2025-08-07 | 350 | 215 | 565 | 2 | 1 | True |  |
| 46 | openai/Azure/gpt-5-mini-2025-08-07 | 346 | 151 | 497 | 2 | 1 | True |  |
| 47 | openai/Azure/gpt-5-mini-2025-08-07 | 303 | 151 | 454 | 2 | 1 | True |  |
| 48 | openai/Azure/gpt-5-mini-2025-08-07 | 294 | 87 | 381 | 2 | 1 | True |  |
| 49 | openai/Azure/gpt-5-mini-2025-08-07 | 298 | 279 | 577 | 2 | 1 | True |  |

### Run #4 — gsm8k  ·  `20260912002822-3f3e2a21`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-4.1 | 815 | 54 | 869 | 4 | 3 | True |  |
| 1 | openai/Azure/gpt-4.1 | 480 | 66 | 546 | 3 | 2 | True |  |
| 2 | openai/Azure/gpt-4.1 | 1229 | 113 | 1342 | 5 | 4 | False |  |
| 3 | openai/Azure/gpt-4.1 | 459 | 36 | 495 | 3 | 2 | True |  |
| 4 | openai/Azure/gpt-4.1 | 944 | 54 | 998 | 4 | 3 | True |  |

### Run #5 — gsm8k  ·  `20260912001716-258c1474`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 87 | 407 | 2 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 87 | 370 | 2 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 344 | 650 | 2 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 87 | 378 | 2 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 87 | 451 | 2 | 1 | True |  |

### Run #6 — gsm8k  ·  `20260912002010-2faa67bc`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 87 | 407 | 2 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 87 | 370 | 2 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 280 | 586 | 2 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 151 | 442 | 2 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 87 | 451 | 2 | 1 | True |  |

### Run #7 — gsm8k  ·  `20260912002309-1d8fea67`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 87 | 407 | 2 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 87 | 370 | 2 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 280 | 586 | 2 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 151 | 442 | 2 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 87 | 451 | 2 | 1 | True |  |

### Run #8 — gsm8k  ·  `20260912002558-11de2ab5`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 87 | 407 | 2 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 87 | 370 | 2 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 280 | 586 | 2 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 151 | 442 | 2 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 87 | 451 | 2 | 1 | True |  |

### Run #9 — tau2  ·  `20260912003101-52b759c7`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/aws/claude-sonnet-5 | 81022 | 2068 | 83090 | 12 | 11 | True |  |
| 1 | openai/aws/claude-sonnet-5 | 87644 | 1703 | 89347 | 12 | 12 | True |  |
| 2 | openai/aws/claude-sonnet-5 | 71319 | 1511 | 72830 | 9 | 9 | False |  |
| 3 | openai/aws/claude-sonnet-5 | 71226 | 2206 | 73432 | 9 | 9 | True |  |
| 4 | openai/aws/claude-sonnet-5 | 95289 | 2238 | 97527 | 12 | 12 | True |  |
| 5 | openai/aws/claude-sonnet-5 | 101189 | 2541 | 103730 | 12 | 12 | True |  |
| 6 | openai/aws/claude-sonnet-5 | 88311 | 2475 | 90786 | 11 | 11 | True |  |
| 7 | openai/aws/claude-sonnet-5 | 75754 | 2427 | 78181 | 10 | 10 | True |  |
| 8 | openai/aws/claude-sonnet-5 | 94657 | 2366 | 97023 | 12 | 12 | True |  |
| 9 | openai/aws/claude-sonnet-5 | 88097 | 2221 | 90318 | 11 | 11 | True |  |

### Run #10 — tau2  ·  `20260912005325-526994f6`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 1 | openai/aws/claude-sonnet-5 | 73115 | 1579 | 74694 | 10 | 10 | True |  |
| 2 | openai/aws/claude-sonnet-5 | 71381 | 1591 | 72972 | 9 | 9 | True |  |
| 3 | openai/aws/claude-sonnet-5 | 96272 | 2150 | 98422 | 12 | 12 | True |  |
| 4 | openai/aws/claude-sonnet-5 | 79958 | 2058 | 82016 | 10 | 10 | False |  |
| 5 | openai/aws/claude-sonnet-5 | 99729 | 2194 | 101923 | 12 | 12 | True |  |
| 6 | openai/aws/claude-sonnet-5 | 88558 | 1945 | 90503 | 11 | 11 | True |  |
| 7 | openai/aws/claude-sonnet-5 | 88234 | 2425 | 90659 | 11 | 11 | True |  |
| 8 | openai/aws/claude-sonnet-5 | 88819 | 2307 | 91126 | 11 | 11 | True |  |
| 9 | openai/aws/claude-sonnet-5 | 88497 | 2642 | 91139 | 11 | 11 | True |  |
| 10 | openai/aws/claude-sonnet-5 | 54379 | 1385 | 55764 | 10 | 10 | True |  |
| 11 | openai/aws/claude-sonnet-5 | 65863 | 2108 | 67971 | 10 | 12 | True |  |
| 12 | openai/aws/claude-sonnet-5 | 51718 | 1477 | 53195 | 9 | 9 | True |  |
| 13 | openai/aws/claude-sonnet-5 | 65089 | 1221 | 66310 | 10 | 12 | True |  |
| 14 | openai/aws/claude-sonnet-5 | 49777 | 1247 | 51024 | 8 | 10 | True |  |
| 15 | openai/aws/claude-sonnet-5 | 91278 | 1858 | 93136 | 12 | 12 | True |  |
| 16 | openai/aws/claude-sonnet-5 | 72298 | 1805 | 74103 | 10 | 10 | True |  |
| 17 | openai/aws/claude-sonnet-5 | 62146 | 916 | 63062 | 11 | 11 | True |  |
| 18 | openai/aws/claude-sonnet-5 | 111498 | 1920 | 113418 | 15 | 15 | False |  |
| 19 | openai/aws/claude-sonnet-5 | 86378 | 2802 | 89180 | 11 | 11 | False |  |

### Run #11 — appworld  ·  `20260912010713-8a93e497`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 3d9a636_1 | openai/gemini-2.5-pro | 203146 | 25417 | 228563 | 27 | 13 | False |  |
| 3d9a636_2 | openai/gemini-2.5-pro | 388393 | 35129 | 423522 | 37 | 18 | False |  |
| 3d9a636_3 | openai/gemini-2.5-pro | 398663 | 31603 | 430266 | 31 | 15 | False |  |
| fd1f8fa_1 | openai/gemini-2.5-pro | 500327 | 42298 | 542625 | 49 | 24 | False |  |
| fd1f8fa_2 | openai/gemini-2.5-pro | 407418 | 34569 | 441987 | 33 | 16 | False |  |

### Run #12 — appworld  ·  `20260912014605-2c6c751c`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 3d9a636_1 | openai/gemini-2.5-pro | 333232 | 26594 | 359826 | 39 | 19 | False |  |
| 3d9a636_2 | openai/gemini-2.5-pro | 194686 | 21446 | 216132 | 21 | 10 | False |  |
| 3d9a636_3 | openai/gemini-2.5-pro | 90946 | 12503 | 103449 | 15 | 6 | None |  |
| 21abae1_1 | openai/gemini-2.5-pro | 92271 | 13247 | 105518 | 15 | 7 | False |  |
| 21abae1_2 | openai/gemini-2.5-pro | 11682 | 2630 | 14312 | 3 | 0 | None |  |
| 21abae1_3 | openai/gemini-2.5-pro | 90690 | 11502 | 102192 | 15 | 7 | False |  |
| 29a7b7e_1 | openai/gemini-2.5-pro | 209140 | 30241 | 239381 | 25 | 12 | False |  |
| 29a7b7e_2 | openai/gemini-2.5-pro | 212247 | 29435 | 241682 | 29 | 14 | False |  |
| 29a7b7e_3 | openai/gemini-2.5-pro | 515173 | 49756 | 564929 | 53 | 26 | False |  |
| 325d6ec_1 | openai/gemini-2.5-pro | 209832 | 18661 | 228493 | 31 | 15 | False |  |
| 325d6ec_2 | openai/gemini-2.5-pro | 207997 | 15906 | 223903 | 29 | 14 | False |  |
| 325d6ec_3 | openai/gemini-2.5-pro | 276296 | 23536 | 299832 | 37 | 18 | False |  |
| 634f342_1 | openai/gemini-2.5-pro | 589611 | 47478 | 637089 | 47 | 23 | False |  |
| 634f342_2 | openai/gemini-2.5-pro | 397737 | 31394 | 429131 | 39 | 19 | False |  |
| 8749218_1 | openai/gemini-2.5-pro | 665309 | 33779 | 699088 | 41 | 20 | False |  |
| 8749218_2 | openai/gemini-2.5-pro | 142924 | 20412 | 163336 | 19 | 9 | False |  |
| fd1f8fa_1 | openai/gemini-2.5-pro | 370885 | 36632 | 407517 | 33 | 16 | False |  |
| fd1f8fa_3 | openai/gemini-2.5-pro | 552237 | 46218 | 598455 | 51 | 25 | False |  |


## 8. Per-task span inventory

Which spans each task actually invoked, by name. §6 and §7 report *counts*; this is what they were counted from, read straight out of each run's `span_report.ndjson`.

Read the **chat** column first. Every task issues a `max_tokens=1` capability probe plus its real model calls, so a healthy task shows **at least 2** chat spans. Exactly **1** means the usage-bearing span for the real call was never written — the warm-agent defect — and that is visible here as a span *count*, independent of any token value (which is what makes it catchable on claude-sonnet-5 and gemini-2.5-pro, where the surviving probe reports a non-zero 8/1 or 1/0).

`not counted` are spans the aggregator cannot see: it only folds a chat/tool span into `llm_count`/`tool_count` when its parent is the `invoke_agent` span, so anything nested deeper is real work missing from the totals. A non-zero figure there is not a bug by itself — it is the known blind spot, now measurable.

The **names** column lists only harness-named spans (`root`/`phase`/`agent`/`chat`/`tool`). The `other` column counts the rest — A2A/HTTP framework internals such as `EventQueue.dequeue_event`, which dominate the raw count (a single gsm8k task emits ~98 spans, ~90 of them framework noise) and would swamp this table. They are all present in `span_report.ndjson`; this section is the readable summary, not the full tree.

### Run #1 — gsm8k  ·  `20260912000728-c0d99c5a`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 118 | 2 | 2 | 109 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #2 — gsm8k  ·  `20260912000921-e6358837`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 93 | 2 | 2 | 84 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 95 | 2 | 2 | 86 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 116 | 2 | 2 | 107 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 99 | 2 | 2 | 90 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 96 | 2 | 2 | 87 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 5 | 95 | 2 | 2 | 86 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 6 | 97 | 2 | 2 | 88 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 7 | 106 | 2 | 2 | 97 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 8 | 118 | 2 | 2 | 109 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 9 | 98 | 2 | 2 | 89 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #3 — gsm8k  ·  `20260912001200-309fc2b2`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 94 | 2 | 2 | 85 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 104 | 2 | 2 | 95 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 104 | 2 | 2 | 95 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 95 | 2 | 2 | 86 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 92 | 2 | 2 | 83 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 5 | 92 | 2 | 2 | 83 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 6 | 92 | 2 | 2 | 83 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 7 | 93 | 2 | 2 | 84 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 8 | 92 | 2 | 2 | 83 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 9 | 92 | 2 | 2 | 83 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 10 | 95 | 2 | 2 | 86 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 11 | 112 | 2 | 2 | 103 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 12 | 117 | 2 | 2 | 108 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 13 | 114 | 2 | 2 | 105 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 14 | 110 | 2 | 2 | 101 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 15 | 142 | 2 | 2 | 133 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 16 | 142 | 2 | 2 | 133 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 17 | 145 | 2 | 2 | 136 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 18 | 136 | 2 | 2 | 127 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 19 | 108 | 2 | 2 | 99 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 20 | 110 | 2 | 2 | 101 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 21 | 107 | 2 | 2 | 98 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 22 | 117 | 2 | 2 | 108 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 23 | 132 | 2 | 2 | 123 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 24 | 131 | 2 | 2 | 122 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 25 | 130 | 2 | 2 | 121 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 26 | 116 | 2 | 2 | 107 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 27 | 105 | 2 | 2 | 96 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 28 | 105 | 2 | 2 | 96 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 29 | 107 | 2 | 2 | 98 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 30 | 107 | 2 | 2 | 98 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 31 | 133 | 2 | 2 | 124 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 32 | 133 | 2 | 2 | 124 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 33 | 131 | 2 | 2 | 122 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 34 | 131 | 2 | 2 | 122 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 35 | 104 | 2 | 2 | 95 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 36 | 105 | 2 | 2 | 96 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 37 | 111 | 2 | 2 | 102 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 38 | 106 | 2 | 2 | 97 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 39 | 137 | 2 | 2 | 128 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 40 | 135 | 2 | 2 | 126 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 41 | 134 | 2 | 2 | 125 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 42 | 129 | 2 | 2 | 120 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 43 | 105 | 2 | 2 | 96 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 44 | 108 | 2 | 2 | 99 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 45 | 103 | 2 | 2 | 94 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 46 | 103 | 2 | 2 | 94 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 47 | 134 | 2 | 2 | 125 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 48 | 133 | 2 | 2 | 124 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 49 | 131 | 2 | 2 | 122 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #4 — gsm8k  ·  `20260912002822-3f3e2a21`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 136 | 4 | 4 | 123 | 1 | `chat Azure/gpt-4.1` x4, `execute_tool calculate_expression` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 116 | 3 | 3 | 105 | 1 | `chat Azure/gpt-4.1` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool calculate_expression`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 146 | 5 | 5 | 131 | 1 | `chat Azure/gpt-4.1` x5, `execute_tool calculate_expression` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 111 | 3 | 3 | 100 | 1 | `chat Azure/gpt-4.1` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool calculate_expression`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 121 | 4 | 4 | 108 | 1 | `chat Azure/gpt-4.1` x4, `execute_tool calculate_expression` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #5 — gsm8k  ·  `20260912001716-258c1474`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 99 | 2 | 2 | 90 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 103 | 2 | 2 | 94 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 102 | 2 | 2 | 93 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 97 | 2 | 2 | 88 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 107 | 2 | 2 | 98 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #6 — gsm8k  ·  `20260912002010-2faa67bc`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 123 | 2 | 2 | 114 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 115 | 2 | 2 | 106 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 106 | 2 | 2 | 97 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 114 | 2 | 2 | 105 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 103 | 2 | 2 | 94 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #7 — gsm8k  ·  `20260912002309-1d8fea67`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 107 | 2 | 2 | 98 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 99 | 2 | 2 | 90 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 109 | 2 | 2 | 100 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 109 | 2 | 2 | 100 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 99 | 2 | 2 | 90 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #8 — gsm8k  ·  `20260912002558-11de2ab5`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 114 | 2 | 2 | 105 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 113 | 2 | 2 | 104 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 109 | 2 | 2 | 100 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 97 | 2 | 2 | 88 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 107 | 2 | 2 | 98 | 1 | `chat Azure/gpt-5-mini-2025-08-07` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #9 — tau2  ·  `20260912003101-52b759c7`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 418 | 12 | 12 | 389 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x5, `execute_tool get_order_details` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 1 | 453 | 12 | 13 | 423 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x6, `execute_tool get_order_details` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 2 | 377 | 9 | 10 | 353 | 1 | `chat aws/claude-sonnet-5` x9, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 3 | 372 | 9 | 10 | 348 | 1 | `chat aws/claude-sonnet-5` x9, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 4 | 498 | 12 | 13 | 468 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 5 | 479 | 12 | 13 | 449 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 6 | 444 | 11 | 12 | 416 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 7 | 432 | 10 | 11 | 406 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 8 | 424 | 12 | 13 | 394 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 9 | 485 | 11 | 12 | 457 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |

### Run #10 — tau2  ·  `20260912005325-526994f6`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 1 | 387 | 10 | 11 | 361 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 2 | 367 | 9 | 10 | 343 | 1 | `chat aws/claude-sonnet-5` x9, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 3 | 398 | 12 | 13 | 368 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 4 | 356 | 10 | 11 | 330 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool get_order_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 5 | 521 | 12 | 13 | 491 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 6 | 379 | 11 | 12 | 351 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 7 | 403 | 11 | 12 | 375 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 8 | 397 | 11 | 12 | 369 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 9 | 409 | 11 | 12 | 381 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 10 | 436 | 10 | 11 | 410 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool transfer_to_human_agents`, `Evaluator.Evaluate` |
| 11 | 408 | 10 | 13 | 380 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x8, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 12 | 339 | 9 | 10 | 315 | 1 | `chat aws/claude-sonnet-5` x9, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool transfer_to_human_agents`, `Evaluator.Evaluate` |
| 13 | 377 | 10 | 13 | 349 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x8, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 14 | 380 | 8 | 11 | 356 | 1 | `chat aws/claude-sonnet-5` x8, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 15 | 416 | 12 | 13 | 386 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 16 | 373 | 10 | 11 | 347 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool cancel_pending_order`, `Evaluator.Evaluate` |
| 17 | 435 | 11 | 12 | 407 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `execute_tool get_order_details` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool modify_pending_order_address`, `Evaluator.Evaluate` |
| 18 | 465 | 15 | 16 | 429 | 1 | `chat aws/claude-sonnet-5` x15, `execute_tool message` x10, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 19 | 456 | 11 | 12 | 428 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool return_delivered_order_items`, `execute_tool transfer_to_human_agents`, `Evaluator.Evaluate` |

### Run #11 — appworld  ·  `20260912010713-8a93e497`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 3d9a636_1 | 952 | 27 | 14 | 906 | 1 | `chat gemini-2.5-pro` x27, `execute_tool phone__search_contacts` x3, `execute_tool venmo__search_friends` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool supervisor__show_profile`, `execute_tool phone__login`, `execute_tool phone__show_contact_relationships`, `execute_tool venmo__add_friend`, `execute_tool venmo__remove_friend`, `execute_tool finish`, `Evaluator.Evaluate` |
| 3d9a636_2 | 1278 | 37 | 19 | 1217 | 1 | `chat gemini-2.5-pro` x37, `execute_tool phone__search_contacts` x10, `execute_tool venmo__search_friends` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool phone__show_contact_relationships`, `execute_tool venmo__add_friend`, `execute_tool finish`, `Evaluator.Evaluate` |
| 3d9a636_3 | 1350 | 31 | 16 | 1298 | 1 | `chat gemini-2.5-pro` x31, `execute_tool phone__search_contacts` x3, `execute_tool venmo__search_friends` x3, `execute_tool venmo__add_friend` x3, `execute_tool phone__login` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool phone__show_contact_relationships`, `execute_tool finish`, `Evaluator.Evaluate` |
| fd1f8fa_1 | 1497 | 49 | 25 | 1418 | 1 | `chat gemini-2.5-pro` x49, `execute_tool spotify__remove_song_from_queue` x11, `execute_tool spotify__play_music` x4, `execute_tool spotify__show_liked_songs` x3, `execute_tool spotify__show_song_queue` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool spotify__next_song`, `execute_tool finish`, `Evaluator.Evaluate` |
| fd1f8fa_2 | 1209 | 33 | 17 | 1154 | 1 | `chat gemini-2.5-pro` x33, `execute_tool spotify__play_music` x5, `execute_tool spotify__show_song_queue` x3, `execute_tool spotify__show_liked_songs` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool spotify__remove_song_from_queue`, `execute_tool spotify__next_song`, `execute_tool spotify__show_current_song`, `execute_tool finish`, `Evaluator.Evaluate` |

### Run #12 — appworld  ·  `20260912014605-2c6c751c`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 3d9a636_1 | 1076 | 39 | 20 | 1012 | 1 | `chat gemini-2.5-pro` x39, `execute_tool venmo__remove_friend` x6, `execute_tool venmo__add_friend` x4, `execute_tool phone__search_contacts` x2, `execute_tool venmo__search_friends` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool phone__login`, `execute_tool venmo__login`, `execute_tool phone__show_contact_relationships`, `execute_tool finish`, `Evaluator.Evaluate` |
| 3d9a636_2 | 864 | 21 | 11 | 827 | 1 | `chat gemini-2.5-pro` x21, `execute_tool phone__login` x2, `execute_tool venmo__search_friends` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool phone__show_contact_relationships`, `execute_tool phone__search_contacts`, `execute_tool venmo__add_friend`, `execute_tool venmo__remove_friend`, `execute_tool finish`, `Evaluator.Evaluate` |
| 3d9a636_3 | 599 | 15 | 7 | 573 | 1 | `chat gemini-2.5-pro` x15, `execute_tool phone__login` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool supervisor__show_profile`, `execute_tool phone__show_contact_relationships`, `execute_tool phone__search_contacts` |
| 21abae1_1 | 505 | 15 | 8 | 477 | 1 | `chat gemini-2.5-pro` x15, `execute_tool venmo__show_transactions` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool phone__get_current_date_and_time`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| 21abae1_2 | 145 | 3 | 1 | 137 | 1 | `chat gemini-2.5-pro` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation` |
| 21abae1_3 | 440 | 15 | 8 | 412 | 1 | `chat gemini-2.5-pro` x15, `execute_tool venmo__show_transactions` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool phone__get_current_date_and_time`, `execute_tool finish`, `Evaluator.Evaluate` |
| 29a7b7e_1 | 985 | 25 | 13 | 942 | 1 | `chat gemini-2.5-pro` x25, `execute_tool file_system__move_file` x4, `execute_tool file_system__show_directory` x3, `execute_tool file_system__create_directory` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool file_system__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| 29a7b7e_2 | 951 | 29 | 15 | 902 | 1 | `chat gemini-2.5-pro` x29, `execute_tool file_system__login` x6, `execute_tool file_system__show_directory` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool file_system__show_account`, `execute_tool finish`, `Evaluator.Evaluate` |
| 29a7b7e_3 | 1586 | 53 | 27 | 1501 | 1 | `chat gemini-2.5-pro` x53, `execute_tool file_system__move_file` x20, `execute_tool file_system__create_directory` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool file_system__login`, `execute_tool file_system__show_directory`, `execute_tool finish`, `Evaluator.Evaluate` |
| 325d6ec_1 | 820 | 31 | 16 | 768 | 1 | `chat gemini-2.5-pro` x31, `execute_tool spotify__show_song_privates` x6, `execute_tool spotify__previous_song` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool spotify__show_current_song`, `execute_tool finish`, `Evaluator.Evaluate` |
| 325d6ec_2 | 755 | 29 | 15 | 706 | 1 | `chat gemini-2.5-pro` x29, `execute_tool spotify__show_current_song` x5, `execute_tool spotify__next_song` x4, `execute_tool spotify__login` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__show_downloaded_songs`, `execute_tool finish`, `Evaluator.Evaluate` |
| 325d6ec_3 | 923 | 37 | 19 | 862 | 1 | `chat gemini-2.5-pro` x37, `execute_tool spotify__show_current_song` x5, `execute_tool spotify__show_song_privates` x5, `execute_tool spotify__next_song` x4, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool spotify__pause_music`, `execute_tool finish`, `Evaluator.Evaluate` |
| 634f342_1 | 1746 | 47 | 24 | 1670 | 1 | `chat gemini-2.5-pro` x47, `execute_tool spotify__show_song` x6, `execute_tool spotify__remove_song_from_playlist` x6, `execute_tool spotify__show_playlist_library` x3, `execute_tool file_system__show_file` x2, `execute_tool file_system__show_directory` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool file_system__login`, `execute_tool spotify__create_playlist`, `execute_tool finish`, `Evaluator.Evaluate` |
| 634f342_2 | 1090 | 39 | 20 | 1026 | 1 | `chat gemini-2.5-pro` x39, `execute_tool spotify__show_playlist_library` x4, `execute_tool file_system__show_directory` x3, `execute_tool spotify__search_artists` x3, `execute_tool spotify__search_songs` x3, `execute_tool file_system__show_file` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool spotify__create_playlist`, `execute_tool finish`, `Evaluator.Evaluate` |
| 8749218_1 | 1477 | 41 | 21 | 1410 | 1 | `chat gemini-2.5-pro` x41, `execute_tool spotify__play_music` x5, `execute_tool spotify__show_recommendations` x4, `execute_tool spotify__add_to_queue` x3, `execute_tool spotify__add_song_to_playlist` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool spotify__clear_song_queue`, `execute_tool spotify__create_playlist`, `execute_tool spotify__show_playlist`, `execute_tool finish`, `Evaluator.Evaluate` |
| 8749218_2 | 758 | 19 | 10 | 724 | 1 | `chat gemini-2.5-pro` x19, `execute_tool spotify__add_to_queue` x2, `execute_tool spotify__play_music` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool spotify__clear_song_queue`, `execute_tool spotify__show_recommendations`, `execute_tool finish`, `Evaluator.Evaluate` |
| fd1f8fa_1 | 1165 | 33 | 17 | 1110 | 1 | `chat gemini-2.5-pro` x33, `execute_tool spotify__play_music` x5, `execute_tool spotify__remove_song_from_queue` x3, `execute_tool spotify__show_liked_songs` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool spotify__show_song_queue`, `execute_tool spotify__next_song`, `execute_tool spotify__show_current_song`, `execute_tool finish`, `Evaluator.Evaluate` |
| fd1f8fa_3 | 1444 | 51 | 26 | 1362 | 1 | `chat gemini-2.5-pro` x51, `execute_tool spotify__remove_song_from_queue` x11, `execute_tool spotify__play_music` x5, `execute_tool spotify__show_liked_songs` x3, `execute_tool spotify__show_song_queue` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool spotify__next_song`, `execute_tool finish`, `Evaluator.Evaluate` |

