# AutoBench Service — 12 Parameterized Runs (KinD — single-node local cluster)

**Report generated:** 2026-09-21T00:12:46Z  
**Service version:** `v1.28`  
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

Objects are readable **and listable anonymously** (no credentials needed), so treat anything written here as public. What is actually exposed: the **keys** carry the caller's username and the Keycloak issuer host, and `run.json` / `report.ndjson` can carry **exception strings** from failed tasks (`status_message`, `TaskResult.error`), which may quote a server error body. No artifact contains task prompts or model outputs — the record schema is entirely ids, counts and durations, and `span_report.*` is restricted to a fixed whitelist of structural and numeric span fields for exactly this reason.

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
| `request_max_tokens` | The `max_tokens` the agent asked for, `null` when it asked for none (the normal case). A value of `1` marks a capability probe rather than real work: agents up to `exgentic 0.3.5.dev131` issued one per task and it was counted as an LLM call, while `dev145` replaced it with an unbilled `GET /v1/models` check that emits no span. **This run set is from after that change**: none of its 773 `chat` spans carries `max_tokens=1`, so its `llm` counts are real calls one-for-one with no offset to subtract. The column is the unambiguous way to tell a probe from a real call, and the probe's code path still exists upstream (`strict=True` in the agent's `health.py`), so it stays. |

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

| # | Benchmark | run_id | Model in use | Status | Pass | Eval-pass | Err/Total | Probe | Wall (s) |
|---|---|---|---|---|---:|---:|---:|---:|---:|
| 1 | gsm8k | `20260915141601-64005f64` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 1 | 0/1 | 0 | 10 |
| 2 | gsm8k | `20260915150408-bdb8490a` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 10 | 0/10 | 0 | 46 |
| 3 | gsm8k | `20260915152135-8654daf6` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 50 | 0/50 | 0 | 62 |
| 4 | gsm8k | `20260915161823-922d61cb` | openai/Azure/gpt-4.1 | succeeded | 1.0 | 5 | 0/5 | 0 | 18 |
| 5 | gsm8k | `20260915155406-558401b8` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 5 | 0/5 | 0 | 15 |
| 6 | gsm8k | `20260915161627-96def03d` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 0.8 | 4 | 1/5 | 0 | 17 |
| 7 | gsm8k | `20260915163353-b8322aee` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 5 | 0/5 | 0 | 13 |
| 8 | gsm8k | `20260915165115-215f1ac6` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 5 | 0/5 | 0 | 18 |
| 9 | tau2 | `20260915155643-e7942de6` | openai/aws/claude-sonnet-5 | succeeded | 1.0 | 10 | 0/10 | 0 | 1052 |
| 10 | tau2 | `20260915150720-b52c0902` | openai/aws/claude-sonnet-5 | succeeded | 0.8 | 16 | 0/20 | 0 | 443 |
| 11 | appworld | `20260915152437-65fbfeb4` | openai/gemini-2.5-pro | succeeded | 0.0 | 0 | 1/5 | 0 | 1638 |
| 12 | appworld | `20260915141808-a74ebc77` | openai/gemini-2.5-pro | succeeded | 0.0 | 0 | 10/20 | 0 | 2642 |

**Tasks with no `report.ndjson` row: #11 (1 of 5), #12 (10 of 20).** These ended before the Service wrote a per-task row — on appworld, the per-task timeout. `Err/Total` counts them (it reads `run.json`, which has one result per task unconditionally), but **§6-§7 cannot**: a missing row contributes to no median, mean or CV, so those per-task statistics describe the tasks that finished rather than the tasks that were attempted.

**Token attribution:** complete — no row lost its usage-bearing span.

**Health probe:** every task reached the model — no task was lost to the agent's per-task `GET /v1/models` check.

**✅ These runs were spaced to defeat the gateway's completion cache.** The legs do repeat tasks — gsm8k #1/#2/#3/#5/#6/#7/#8 share 10 task ids; tau2 #9/#10 share 10 task ids; appworld on `openai/gemini-2.5-pro` #11/#12 share 3 task ids — but the driver rested every (benchmark, model) prompt set for at least **900 s** before reusing it, against a measured cache TTL of ~10 min, so **no leg could replay another's completions**: 4 leg(s) were the first of their prompt set and had nothing to replay, 5 had the gap covered by other legs running in between, and 3 waited out the remainder explicitly. The gap is measured from the previous leg's *finish*, which errs safe — a shared task set is always a prefix, so the colliding prompts were sent near that leg's start and are older still. Per-call latency and output tokens are therefore independent across these legs, which was not true of earlier matrices. For the underlying mechanism: A repeated request body comes back from `ete-litellm` as the *same stored response* — identical response `id`, identical `usage` — measured with the agent bypassed, on both clusters' gateways. **The TTL is ~10 minutes**, measured by survival curve (one probe per nonce at its own age: hit at 3/5/7/9 min, miss at 11/13/15/18/21). A replay re-reports the stored token counts and its latency is a cache lookup, so per-call latency and output token counts are affected; input tokens and pass rates are not (the same prompt and the same correct answer either way). This is not an agent setting — `EXGENTIC_LITELLM_CACHING=false` is pinned and provably inert against it — and **latency is not a reliable hit detector**: one measured replay took 3.0 s, the same as a miss. Compare response `id`s. Details in `docs/exgentic-agent-bug-report-20260901.md`.

## 5. Contents of the manifest file

Every run writes a `manifest.json` at its S3 prefix — a self-describing index of the run's data objects, so a client discovers the whole run in one fetch with no S3 listing. It lists the 7 data artifacts (`run.json`, `report.ndjson`, `token_report.ndjson`, `span_report.ndjson`, `report.parquet`, `token_report.parquet`, `span_report.parquet`); the manifest does **not** list itself. Each entry carries `name`, `format`, `key`, public `url`, and `size_bytes`.

Example:

```json
{
  "run_id": "20260915141601-64005f64",
  "benchmark": "gsm8k",
  "prefix": "kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20260915141601-64005f64",
  "artifacts": [
    {
      "name": "run.json",
      "format": "json",
      "key": "kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20260915141601-64005f64/run.json",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20260915141601-64005f64/run.json",
      "size_bytes": 470
    },
    {
      "name": "report.ndjson",
      "format": "ndjson",
      "key": "kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20260915141601-64005f64/report.ndjson",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20260915141601-64005f64/report.ndjson",
      "size_bytes": 1029
    },
    {
      "name": "token_report.ndjson",
      "format": "ndjson",
      "key": "kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20260915141601-64005f64/token_report.ndjson",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20260915141601-64005f64/token_report.ndjson",
      "size_bytes": 371
    },
    {
      "name": "span_report.ndjson",
      "format": "ndjson",
      "key": "kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20260915141601-64005f64/span_report.ndjson",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20260915141601-64005f64/span_report.ndjson",
      "size_bytes": 57142
    },
    {
      "name": "report.parquet",
      "format": "parquet",
      "key": "kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20260915141601-64005f64/report.parquet",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20260915141601-64005f64/report.parquet",
      "size_bytes": 11984
    },
    {
      "name": "token_report.parquet",
      "format": "parquet",
      "key": "kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20260915141601-64005f64/token_report.parquet",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20260915141601-64005f64/token_report.parquet",
      "size_bytes": 4425
    },
    {
      "name": "span_report.parquet",
      "format": "parquet",
      "key": "kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20260915141601-64005f64/span_report.parquet",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/gsm8k/20260915141601-64005f64/span_report.parquet",
      "size_bytes": 10830
    }
  ]
}
```


## 6. Per-run aggregate — input & output tokens

`median` then `mean` then `CV` for each direction. median is the robust centre, mean the arithmetic average, and a mean well above the median signals right-skew (a few long tasks). `CV` is population sigma / mean, i.e. how unevenly the token cost is spread; it shares its denominator with `mean`, not `median`. CV is `—` for single-task runs.

A `⚠` in the Lost column means some of that run's rows lost their usage-bearing span (§2), so **every token figure on that row is understated** — including the median/mean/CV, which are computed over all tasks and so are dragged down by the damaged ones. Do not quote them as the run's cost.

| # | Benchmark | Model | Tasks | Lost | LLM calls | Input | Output | Total | IN median | IN mean | IN CV | OUT median | OUT mean | OUT CV |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 1 | 0 | 1 | 320 | 470 | 790 | 320 | 320 | — | 470 | 470 | — |
| 2 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 10 | 0 | 10 | 3166 | 1949 | 5115 | 312 | 317 | 0.08 | 182 | 195 | 0.67 |
| 3 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 50 | 0 | 50 | 15684 | 8654 | 24338 | 310 | 314 | 0.06 | 150 | 173 | 0.76 |
| 4 | gsm8k | openai/Azure/gpt-4.1 | 5 | 0 | 15 | 4183 | 317 | 4500 | 807 | 837 | 0.44 | 63 | 63 | 0.33 |
| 5 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 5 | 0 | 5 | 1564 | 1775 | 3339 | 306 | 313 | 0.09 | 342 | 355 | 0.59 |
| 6 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 5 | 0 | 5 | 1564 | 751 | 2315 | 306 | 313 | 0.09 | 86 | 150 | 0.85 |
| 7 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 5 | 0 | 5 | 1564 | 815 | 2379 | 306 | 313 | 0.09 | 86 | 163 | 0.76 |
| 8 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 5 | 0 | 5 | 1564 | 1071 | 2635 | 306 | 313 | 0.09 | 86 | 214 | 0.76 |
| 9 | tau2 | openai/aws/claude-sonnet-5 | 10 | 0 | 120 | 1019232 | 23194 | 1042426 | 101678 | 101923 | 0.14 | 2315 | 2319 | 0.17 |
| 10 | tau2 | openai/aws/claude-sonnet-5 | 20 | 0 | 225 | 1743312 | 38627 | 1781939 | 90858 | 87166 | 0.20 | 1986 | 1931 | 0.28 |
| 11 | appworld | openai/gemini-2.5-pro | 4 | 0 | 100 | 840030 | 85112 | 925142 | 202352 | 210008 | 0.19 | 19973 | 21278 | 0.16 |
| 12 | appworld | openai/gemini-2.5-pro | 10 | 0 | 232 | 2009348 | 189098 | 2198446 | 178278 | 200935 | 0.69 | 16866 | 18910 | 0.52 |

## 7. Per-task detail

`model` is repeated on every row deliberately: token counts are only comparable within a model, and the runs do not all use the same one (#4 swaps the gsm8k model, so its ~815 input tokens per task are not comparable with the ~320 of #1–3/#5–8 on the same tasks).

`tool` is shown next to `llm` because their relationship is the tell for a damaged row: each tool call needs a preceding model turn, so `llm=1` beside `tool=11` cannot be real work — it is a lost span (§2), flagged `⚠`.

### Run #1 — gsm8k  ·  `20260915141601-64005f64`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 470 | 790 | 1 | 1 | True |  |

### Run #2 — gsm8k  ·  `20260915150408-bdb8490a`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 86 | 406 | 1 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 86 | 369 | 1 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 279 | 585 | 1 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 470 | 761 | 1 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 22 | 386 | 1 | 1 | True |  |
| 5 | openai/Azure/gpt-5-mini-2025-08-07 | 309 | 150 | 459 | 1 | 1 | True |  |
| 6 | openai/Azure/gpt-5-mini-2025-08-07 | 298 | 342 | 640 | 1 | 1 | True |  |
| 7 | openai/Azure/gpt-5-mini-2025-08-07 | 323 | 214 | 537 | 1 | 1 | True |  |
| 8 | openai/Azure/gpt-5-mini-2025-08-07 | 358 | 214 | 572 | 1 | 1 | True |  |
| 9 | openai/Azure/gpt-5-mini-2025-08-07 | 314 | 86 | 400 | 1 | 1 | True |  |

### Run #3 — gsm8k  ·  `20260915152135-8654daf6`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 150 | 470 | 1 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 86 | 369 | 1 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 279 | 585 | 1 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 470 | 761 | 1 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 86 | 450 | 1 | 1 | True |  |
| 5 | openai/Azure/gpt-5-mini-2025-08-07 | 309 | 150 | 459 | 1 | 1 | True |  |
| 6 | openai/Azure/gpt-5-mini-2025-08-07 | 298 | 86 | 384 | 1 | 1 | True |  |
| 7 | openai/Azure/gpt-5-mini-2025-08-07 | 323 | 342 | 665 | 1 | 1 | True |  |
| 8 | openai/Azure/gpt-5-mini-2025-08-07 | 358 | 214 | 572 | 1 | 1 | True |  |
| 9 | openai/Azure/gpt-5-mini-2025-08-07 | 314 | 86 | 400 | 1 | 1 | True |  |
| 10 | openai/Azure/gpt-5-mini-2025-08-07 | 315 | 86 | 401 | 1 | 1 | True |  |
| 11 | openai/Azure/gpt-5-mini-2025-08-07 | 316 | 86 | 402 | 1 | 1 | True |  |
| 12 | openai/Azure/gpt-5-mini-2025-08-07 | 322 | 278 | 600 | 1 | 1 | True |  |
| 13 | openai/Azure/gpt-5-mini-2025-08-07 | 315 | 214 | 529 | 1 | 1 | True |  |
| 14 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 150 | 456 | 1 | 1 | True |  |
| 15 | openai/Azure/gpt-5-mini-2025-08-07 | 347 | 86 | 433 | 1 | 1 | True |  |
| 16 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 214 | 520 | 1 | 1 | True |  |
| 17 | openai/Azure/gpt-5-mini-2025-08-07 | 309 | 151 | 460 | 1 | 1 | True |  |
| 18 | openai/Azure/gpt-5-mini-2025-08-07 | 284 | 86 | 370 | 1 | 1 | True |  |
| 19 | openai/Azure/gpt-5-mini-2025-08-07 | 321 | 150 | 471 | 1 | 1 | True |  |
| 20 | openai/Azure/gpt-5-mini-2025-08-07 | 317 | 278 | 595 | 1 | 1 | True |  |
| 21 | openai/Azure/gpt-5-mini-2025-08-07 | 301 | 342 | 643 | 1 | 1 | True |  |
| 22 | openai/Azure/gpt-5-mini-2025-08-07 | 311 | 86 | 397 | 1 | 1 | True |  |
| 23 | openai/Azure/gpt-5-mini-2025-08-07 | 293 | 86 | 379 | 1 | 1 | True |  |
| 24 | openai/Azure/gpt-5-mini-2025-08-07 | 292 | 86 | 378 | 1 | 1 | True |  |
| 25 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 86 | 406 | 1 | 1 | True |  |
| 26 | openai/Azure/gpt-5-mini-2025-08-07 | 321 | 86 | 407 | 1 | 1 | True |  |
| 27 | openai/Azure/gpt-5-mini-2025-08-07 | 310 | 150 | 460 | 1 | 1 | True |  |
| 28 | openai/Azure/gpt-5-mini-2025-08-07 | 304 | 86 | 390 | 1 | 1 | True |  |
| 29 | openai/Azure/gpt-5-mini-2025-08-07 | 324 | 86 | 410 | 1 | 1 | True |  |
| 30 | openai/Azure/gpt-5-mini-2025-08-07 | 292 | 150 | 442 | 1 | 1 | True |  |
| 31 | openai/Azure/gpt-5-mini-2025-08-07 | 317 | 86 | 403 | 1 | 1 | True |  |
| 32 | openai/Azure/gpt-5-mini-2025-08-07 | 297 | 86 | 383 | 1 | 1 | True |  |
| 33 | openai/Azure/gpt-5-mini-2025-08-07 | 285 | 86 | 371 | 1 | 1 | True |  |
| 34 | openai/Azure/gpt-5-mini-2025-08-07 | 297 | 86 | 383 | 1 | 1 | True |  |
| 35 | openai/Azure/gpt-5-mini-2025-08-07 | 305 | 86 | 391 | 1 | 1 | True |  |
| 36 | openai/Azure/gpt-5-mini-2025-08-07 | 297 | 150 | 447 | 1 | 1 | True |  |
| 37 | openai/Azure/gpt-5-mini-2025-08-07 | 315 | 278 | 593 | 1 | 1 | True |  |
| 38 | openai/Azure/gpt-5-mini-2025-08-07 | 300 | 214 | 514 | 1 | 1 | True |  |
| 39 | openai/Azure/gpt-5-mini-2025-08-07 | 329 | 278 | 607 | 1 | 1 | True |  |
| 40 | openai/Azure/gpt-5-mini-2025-08-07 | 308 | 406 | 714 | 1 | 1 | True |  |
| 41 | openai/Azure/gpt-5-mini-2025-08-07 | 379 | 86 | 465 | 1 | 1 | True |  |
| 42 | openai/Azure/gpt-5-mini-2025-08-07 | 336 | 86 | 422 | 1 | 1 | True |  |
| 43 | openai/Azure/gpt-5-mini-2025-08-07 | 310 | 150 | 460 | 1 | 1 | True |  |
| 44 | openai/Azure/gpt-5-mini-2025-08-07 | 326 | 150 | 476 | 1 | 1 | True |  |
| 45 | openai/Azure/gpt-5-mini-2025-08-07 | 350 | 790 | 1140 | 1 | 1 | True |  |
| 46 | openai/Azure/gpt-5-mini-2025-08-07 | 346 | 342 | 688 | 1 | 1 | True |  |
| 47 | openai/Azure/gpt-5-mini-2025-08-07 | 303 | 150 | 453 | 1 | 1 | True |  |
| 48 | openai/Azure/gpt-5-mini-2025-08-07 | 294 | 86 | 380 | 1 | 1 | True |  |
| 49 | openai/Azure/gpt-5-mini-2025-08-07 | 298 | 86 | 384 | 1 | 1 | True |  |

### Run #4 — gsm8k  ·  `20260915161823-922d61cb`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-4.1 | 807 | 50 | 857 | 3 | 3 | True |  |
| 1 | openai/Azure/gpt-4.1 | 472 | 63 | 535 | 2 | 2 | True |  |
| 2 | openai/Azure/gpt-4.1 | 1444 | 91 | 1535 | 5 | 5 | True |  |
| 3 | openai/Azure/gpt-4.1 | 451 | 33 | 484 | 2 | 2 | True |  |
| 4 | openai/Azure/gpt-4.1 | 1009 | 80 | 1089 | 3 | 3 | True |  |

### Run #5 — gsm8k  ·  `20260915155406-558401b8`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 342 | 662 | 1 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 726 | 1009 | 1 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 343 | 649 | 1 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 278 | 569 | 1 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 86 | 450 | 1 | 1 | True |  |

### Run #6 — gsm8k  ·  `20260915161627-96def03d`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 86 | 406 | 1 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 86 | 369 | 1 | 1 | None |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 407 | 713 | 1 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 86 | 377 | 1 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 86 | 450 | 1 | 1 | True |  |

### Run #7 — gsm8k  ·  `20260915163353-b8322aee`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 150 | 470 | 1 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 86 | 369 | 1 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 407 | 713 | 1 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 86 | 377 | 1 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 86 | 450 | 1 | 1 | True |  |

### Run #8 — gsm8k  ·  `20260915165115-215f1ac6`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 86 | 406 | 1 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 86 | 369 | 1 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 343 | 649 | 1 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 470 | 761 | 1 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 86 | 450 | 1 | 1 | True |  |

### Run #9 — tau2  ·  `20260915155643-e7942de6`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/aws/claude-sonnet-5 | 88504 | 1864 | 90368 | 11 | 11 | True |  |
| 1 | openai/aws/claude-sonnet-5 | 104661 | 2077 | 106738 | 14 | 14 | True |  |
| 2 | openai/aws/claude-sonnet-5 | 110448 | 1912 | 112360 | 13 | 13 | True |  |
| 3 | openai/aws/claude-sonnet-5 | 98695 | 1745 | 100440 | 12 | 12 | True |  |
| 4 | openai/aws/claude-sonnet-5 | 91141 | 2945 | 94086 | 10 | 10 | True |  |
| 5 | openai/aws/claude-sonnet-5 | 132247 | 2745 | 134992 | 14 | 14 | True |  |
| 6 | openai/aws/claude-sonnet-5 | 112101 | 2676 | 114777 | 13 | 13 | True |  |
| 7 | openai/aws/claude-sonnet-5 | 105970 | 2242 | 108212 | 12 | 12 | True |  |
| 8 | openai/aws/claude-sonnet-5 | 81251 | 2388 | 83639 | 10 | 10 | True |  |
| 9 | openai/aws/claude-sonnet-5 | 94214 | 2600 | 96814 | 11 | 11 | True |  |

### Run #10 — tau2  ·  `20260915150720-b52c0902`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/aws/claude-sonnet-5 | 92689 | 2065 | 94754 | 12 | 12 | True |  |
| 1 | openai/aws/claude-sonnet-5 | 82293 | 1659 | 83952 | 11 | 11 | True |  |
| 2 | openai/aws/claude-sonnet-5 | 76300 | 1459 | 77759 | 9 | 9 | False |  |
| 3 | openai/aws/claude-sonnet-5 | 101579 | 2130 | 103709 | 12 | 12 | True |  |
| 4 | openai/aws/claude-sonnet-5 | 102028 | 2229 | 104257 | 12 | 12 | True |  |
| 5 | openai/aws/claude-sonnet-5 | 105984 | 2315 | 108299 | 12 | 12 | True |  |
| 6 | openai/aws/claude-sonnet-5 | 93780 | 2175 | 95955 | 11 | 11 | True |  |
| 7 | openai/aws/claude-sonnet-5 | 112494 | 2001 | 114495 | 13 | 13 | True |  |
| 8 | openai/aws/claude-sonnet-5 | 113377 | 3086 | 116463 | 13 | 13 | True |  |
| 9 | openai/aws/claude-sonnet-5 | 93573 | 2619 | 96192 | 11 | 11 | True |  |
| 10 | openai/aws/claude-sonnet-5 | 53770 | 1236 | 55006 | 9 | 9 | True |  |
| 11 | openai/aws/claude-sonnet-5 | 74983 | 1689 | 76672 | 11 | 11 | True |  |
| 12 | openai/aws/claude-sonnet-5 | 78367 | 1972 | 80339 | 12 | 12 | True |  |
| 13 | openai/aws/claude-sonnet-5 | 73193 | 1538 | 74731 | 11 | 11 | True |  |
| 14 | openai/aws/claude-sonnet-5 | 73596 | 1426 | 75022 | 11 | 11 | True |  |
| 15 | openai/aws/claude-sonnet-5 | 106460 | 1894 | 108354 | 13 | 13 | False |  |
| 16 | openai/aws/claude-sonnet-5 | 78658 | 2146 | 80804 | 10 | 10 | True |  |
| 17 | openai/aws/claude-sonnet-5 | 48472 | 690 | 49162 | 8 | 8 | True |  |
| 18 | openai/aws/claude-sonnet-5 | 90124 | 1565 | 91689 | 13 | 13 | False |  |
| 19 | openai/aws/claude-sonnet-5 | 91592 | 2733 | 94325 | 11 | 11 | False |  |

### Run #11 — appworld  ·  `20260915152437-65fbfeb4`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 3d9a636_1 | openai/gemini-2.5-pro | 184582 | 20010 | 204592 | 24 | 12 | False |  |
| 3d9a636_2 | openai/gemini-2.5-pro | 164172 | 18114 | 182286 | 22 | 11 | False |  |
| 3d9a636_3 | openai/gemini-2.5-pro | 220122 | 19936 | 240058 | 26 | 13 | False |  |
| fd1f8fa_1 | openai/gemini-2.5-pro | 271154 | 27052 | 298206 | 28 | 14 | False |  |

### Run #12 — appworld  ·  `20260915141808-a74ebc77`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 3d9a636_1 | openai/gemini-2.5-pro | 200543 | 19035 | 219578 | 20 | 10 | False |  |
| 3d9a636_2 | openai/gemini-2.5-pro | 196530 | 20789 | 217319 | 24 | 12 | False |  |
| 3d9a636_3 | openai/gemini-2.5-pro | 160025 | 14696 | 174721 | 20 | 10 | False |  |
| 21abae1_1 | openai/gemini-2.5-pro | 63272 | 9215 | 72487 | 10 | 5 | False |  |
| 21abae1_2 | openai/gemini-2.5-pro | 62100 | 10475 | 72575 | 10 | 5 | False |  |
| 21abae1_3 | openai/gemini-2.5-pro | 64574 | 8727 | 73301 | 10 | 5 | False |  |
| 325d6ec_1 | openai/gemini-2.5-pro | 295653 | 24089 | 319742 | 38 | 19 | False |  |
| 325d6ec_2 | openai/gemini-2.5-pro | 154378 | 13695 | 168073 | 22 | 11 | False |  |
| 634f342_2 | openai/gemini-2.5-pro | 272001 | 25008 | 297009 | 28 | 14 | False |  |
| fd1f8fa_3 | openai/gemini-2.5-pro | 540272 | 43369 | 583641 | 50 | 25 | False |  |


## 8. Per-task span inventory

Which spans each task actually invoked, by name. §6 and §7 report *counts*; this is what they were counted from, read straight out of each run's `span_report.ndjson`.

Read the **chat** and **tool** columns together. There is no fixed healthy chat count — a one-shot gsm8k task legitimately shows a single `chat` span, and a multi-turn tau2 task shows many. What is not possible is **`chat` ≤ 1 alongside `tool` ≥ 2**: every tool call needs a model turn to request it, so a task cannot invoke two tools off one chat span. That shape means a usage-bearing span was dropped, and this section flags it. Reading it off *counts* rather than token values is what makes it catchable on every model, including ones whose dropped span would still have reported plausible non-zero usage.

⚠ **The `chat` and `tool` columns below are raw span totals; the ⚠ flag is computed on the `counted` subset only.** Do not apply the rule by hand to these columns. A healthy gsm8k task emits two `execute_tool` spans — `initial_observation` and `submit` — of which only `submit` is counted, so its raw pair is `1`/`2` and would trip the test while its §7 pair is the innocent `1`/`1`. Filtering to `counted` is what makes this section agree with §7.

Up to `exgentic 0.3.5.dev131` each task also issued a `max_tokens=1` capability probe, so a bare `chat == 1` used to be the damage signal and **at least 2** was healthy. The probe was replaced in `dev145` by an unbilled `GET /v1/models` check that emits no span — do not resurrect that rule, and do not compare chat counts across runs that straddle the change. **This run set is from after that change**: none of its 773 `chat` spans carries `max_tokens=1`, so its `llm` counts are real calls one-for-one with no offset to subtract.

`not counted` are spans the aggregator cannot see: it only folds a chat/tool span into `llm_count`/`tool_count` when its parent is the `invoke_agent` span, so anything nested deeper is real work missing from the totals. A non-zero figure there is not a bug by itself — it is the known blind spot, now measurable.

The **names** column lists only the spans the Service names (`root`/`phase`) and those the agent names (`agent`/`chat`/`tool`). The `other` column counts the rest — A2A/HTTP framework internals inside the agent pod, such as `EventQueue.dequeue_event` or the ASGI `POST / http send`, which dominate the raw count (a single gsm8k task emits ~98 spans, ~90 of them framework noise) and would swamp this table. They are all present in `span_report.ndjson`; this section is the readable summary, not the full tree.

### Run #1 — gsm8k  ·  `20260915141601-64005f64`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 109 | 1 | 2 | 101 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #2 — gsm8k  ·  `20260915150408-bdb8490a`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 99 | 1 | 2 | 91 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 95 | 1 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 97 | 1 | 2 | 89 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 107 | 1 | 2 | 99 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 97 | 1 | 2 | 89 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 5 | 98 | 1 | 2 | 90 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 6 | 98 | 1 | 2 | 90 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 7 | 95 | 1 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 8 | 98 | 1 | 2 | 90 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 9 | 100 | 1 | 2 | 92 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #3 — gsm8k  ·  `20260915152135-8654daf6`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 95 | 1 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 93 | 1 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 104 | 1 | 2 | 96 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 105 | 1 | 2 | 97 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 94 | 1 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 5 | 96 | 1 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 6 | 102 | 1 | 2 | 94 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 7 | 101 | 1 | 2 | 93 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 8 | 97 | 1 | 2 | 89 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 9 | 96 | 1 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 10 | 95 | 1 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 11 | 102 | 1 | 2 | 94 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 12 | 102 | 1 | 2 | 94 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 13 | 108 | 1 | 2 | 100 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 14 | 106 | 1 | 2 | 98 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 15 | 99 | 1 | 2 | 91 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 16 | 100 | 1 | 2 | 92 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 17 | 104 | 1 | 2 | 96 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 18 | 99 | 1 | 2 | 91 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 19 | 101 | 1 | 2 | 93 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 20 | 104 | 1 | 2 | 96 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 21 | 99 | 1 | 2 | 91 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 22 | 94 | 1 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 23 | 93 | 1 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 24 | 94 | 1 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 25 | 94 | 1 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 26 | 94 | 1 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 27 | 101 | 1 | 2 | 93 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 28 | 99 | 1 | 2 | 91 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 29 | 99 | 1 | 2 | 91 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 30 | 98 | 1 | 2 | 90 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 31 | 98 | 1 | 2 | 90 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 32 | 96 | 1 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 33 | 96 | 1 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 34 | 96 | 1 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 35 | 93 | 1 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 36 | 101 | 1 | 2 | 93 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 37 | 101 | 1 | 2 | 93 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 38 | 100 | 1 | 2 | 92 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 39 | 109 | 1 | 2 | 101 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 40 | 101 | 1 | 2 | 93 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 41 | 96 | 1 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 42 | 95 | 1 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 43 | 98 | 1 | 2 | 90 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 44 | 97 | 1 | 2 | 89 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 45 | 108 | 1 | 2 | 100 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 46 | 100 | 1 | 2 | 92 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 47 | 95 | 1 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 48 | 95 | 1 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 49 | 94 | 1 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #4 — gsm8k  ·  `20260915161823-922d61cb`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 147 | 3 | 4 | 135 | 1 | `chat Azure/gpt-4.1` x3, `execute_tool calculate_expression` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 125 | 2 | 3 | 115 | 1 | `chat Azure/gpt-4.1` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool calculate_expression`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 170 | 5 | 6 | 154 | 1 | `chat Azure/gpt-4.1` x5, `execute_tool calculate_expression` x4, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 119 | 2 | 3 | 109 | 1 | `chat Azure/gpt-4.1` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool calculate_expression`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 127 | 3 | 4 | 115 | 1 | `chat Azure/gpt-4.1` x3, `execute_tool calculate_expression` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #5 — gsm8k  ·  `20260915155406-558401b8`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 110 | 1 | 2 | 102 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 120 | 1 | 2 | 112 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 110 | 1 | 2 | 102 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 104 | 1 | 2 | 96 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 95 | 1 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #6 — gsm8k  ·  `20260915161627-96def03d`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 107 | 1 | 2 | 99 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 102 | 1 | 2 | 95 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit` |
| 2 | 111 | 1 | 2 | 103 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 102 | 1 | 2 | 94 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 110 | 1 | 2 | 102 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #7 — gsm8k  ·  `20260915163353-b8322aee`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 114 | 1 | 2 | 106 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 109 | 1 | 2 | 101 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 102 | 1 | 2 | 94 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 108 | 1 | 2 | 100 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 100 | 1 | 2 | 92 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #8 — gsm8k  ·  `20260915165115-215f1ac6`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 113 | 1 | 2 | 105 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 126 | 1 | 2 | 118 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 106 | 1 | 2 | 98 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 119 | 1 | 2 | 111 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 97 | 1 | 2 | 89 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #9 — tau2  ·  `20260915155643-e7942de6`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 374 | 11 | 12 | 346 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool get_user_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 1 | 475 | 14 | 15 | 441 | 1 | `chat aws/claude-sonnet-5` x14, `execute_tool message` x7, `execute_tool get_order_details` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 2 | 435 | 13 | 14 | 403 | 1 | `chat aws/claude-sonnet-5` x13, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 3 | 441 | 12 | 13 | 411 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 4 | 376 | 10 | 11 | 350 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 5 | 443 | 14 | 15 | 409 | 1 | `chat aws/claude-sonnet-5` x14, `execute_tool message` x9, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 6 | 472 | 13 | 14 | 440 | 1 | `chat aws/claude-sonnet-5` x13, `execute_tool message` x8, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 7 | 431 | 12 | 13 | 401 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 8 | 346 | 10 | 11 | 320 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 9 | 417 | 11 | 12 | 389 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |

### Run #10 — tau2  ·  `20260915150720-b52c0902`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 364 | 12 | 13 | 334 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x6, `execute_tool get_order_details` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 1 | 347 | 11 | 12 | 319 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x5, `execute_tool get_order_details` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 2 | 310 | 9 | 10 | 286 | 1 | `chat aws/claude-sonnet-5` x9, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 3 | 372 | 12 | 13 | 342 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 4 | 370 | 12 | 13 | 340 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 5 | 395 | 12 | 13 | 365 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 6 | 366 | 11 | 12 | 338 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 7 | 459 | 13 | 14 | 427 | 1 | `chat aws/claude-sonnet-5` x13, `execute_tool message` x8, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 8 | 440 | 13 | 14 | 408 | 1 | `chat aws/claude-sonnet-5` x13, `execute_tool message` x8, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 9 | 354 | 11 | 12 | 326 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 10 | 285 | 9 | 10 | 261 | 1 | `chat aws/claude-sonnet-5` x9, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool transfer_to_human_agents`, `Evaluator.Evaluate` |
| 11 | 364 | 11 | 12 | 336 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 12 | 382 | 12 | 13 | 352 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `execute_tool transfer_to_human_agents`, `Evaluator.Evaluate` |
| 13 | 348 | 11 | 12 | 320 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 14 | 362 | 11 | 12 | 334 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 15 | 403 | 13 | 14 | 371 | 1 | `chat aws/claude-sonnet-5` x13, `execute_tool message` x8, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 16 | 353 | 10 | 11 | 327 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool cancel_pending_order`, `Evaluator.Evaluate` |
| 17 | 245 | 8 | 9 | 223 | 1 | `chat aws/claude-sonnet-5` x8, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_order_details`, `execute_tool modify_pending_order_address`, `Evaluator.Evaluate` |
| 18 | 371 | 13 | 14 | 339 | 1 | `chat aws/claude-sonnet-5` x13, `execute_tool message` x9, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 19 | 360 | 11 | 12 | 332 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool return_delivered_order_items`, `execute_tool transfer_to_human_agents`, `Evaluator.Evaluate` |

### Run #11 — appworld  ·  `20260915152437-65fbfeb4`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 3d9a636_1 | 735 | 24 | 13 | 693 | 1 | `chat gemini-2.5-pro` x24, `execute_tool phone__login` x2, `execute_tool phone__search_contacts` x2, `execute_tool venmo__search_friends` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool supervisor__show_profile`, `execute_tool phone__show_contact_relationships`, `execute_tool venmo__add_friend`, `execute_tool venmo__remove_friend`, `execute_tool finish`, `Evaluator.Evaluate` |
| 3d9a636_2 | 783 | 22 | 12 | 744 | 1 | `chat gemini-2.5-pro` x22, `execute_tool phone__search_contacts` x2, `execute_tool venmo__search_friends` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool phone__login`, `execute_tool phone__show_contact_relationships`, `execute_tool venmo__login`, `execute_tool venmo__add_friend`, `execute_tool venmo__remove_friend`, `execute_tool finish`, `Evaluator.Evaluate` |
| 3d9a636_3 | 794 | 26 | 14 | 749 | 1 | `chat gemini-2.5-pro` x26, `execute_tool phone__search_contacts` x3, `execute_tool venmo__search_friends` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool phone__login`, `execute_tool phone__show_contact_relationships`, `execute_tool venmo__show_account`, `execute_tool venmo__add_friend`, `execute_tool venmo__remove_friend`, `execute_tool finish`, `Evaluator.Evaluate` |
| fd1f8fa_1 | 894 | 28 | 15 | 846 | 1 | `chat gemini-2.5-pro` x28, `execute_tool spotify__show_liked_songs` x4, `execute_tool spotify__play_music` x4, `execute_tool spotify__show_song_queue` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool spotify__remove_song_from_queue`, `execute_tool finish`, `Evaluator.Evaluate` |

### Run #12 — appworld  ·  `20260915141808-a74ebc77`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 3d9a636_1 | 779 | 20 | 11 | 743 | 1 | `chat gemini-2.5-pro` x20, `execute_tool venmo__add_friend` x3, `execute_tool phone__login` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool phone__show_contact_relationships`, `execute_tool phone__search_contacts`, `execute_tool venmo__remove_friend`, `execute_tool finish`, `Evaluator.Evaluate` |
| 3d9a636_2 | 749 | 24 | 13 | 707 | 1 | `chat gemini-2.5-pro` x24, `execute_tool phone__search_contacts` x3, `execute_tool venmo__search_friends` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool phone__login`, `execute_tool phone__show_contact_relationships`, `execute_tool venmo__add_friend`, `execute_tool venmo__remove_friend`, `execute_tool finish`, `Evaluator.Evaluate` |
| 3d9a636_3 | 601 | 20 | 11 | 565 | 1 | `chat gemini-2.5-pro` x20, `execute_tool phone__search_contacts` x3, `execute_tool venmo__search_friends` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool venmo__add_friend`, `execute_tool venmo__remove_friend`, `execute_tool finish`, `Evaluator.Evaluate` |
| 21abae1_1 | 466 | 10 | 6 | 445 | 1 | `chat gemini-2.5-pro` x10, `execute_tool venmo__show_transactions` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| 21abae1_2 | 503 | 10 | 6 | 482 | 1 | `chat gemini-2.5-pro` x10, `execute_tool venmo__show_transactions` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| 21abae1_3 | 375 | 10 | 6 | 354 | 1 | `chat gemini-2.5-pro` x10, `execute_tool venmo__show_transactions` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| 325d6ec_1 | 1196 | 38 | 20 | 1133 | 1 | `chat gemini-2.5-pro` x38, `execute_tool spotify__show_song_privates` x6, `execute_tool spotify__previous_song` x6, `execute_tool spotify__show_current_song` x2, `execute_tool spotify__login` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__show_song`, `execute_tool finish`, `Evaluator.Evaluate` |
| 325d6ec_2 | 829 | 22 | 12 | 790 | 1 | `chat gemini-2.5-pro` x22, `execute_tool spotify__next_song` x4, `execute_tool spotify__show_downloaded_songs` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool supervisor__show_profile`, `execute_tool spotify__login`, `execute_tool spotify__show_current_song`, `execute_tool finish`, `Evaluator.Evaluate` |
| 634f342_2 | 1643 | 28 | 15 | 1595 | 1 | `chat gemini-2.5-pro` x28, `execute_tool spotify__show_playlist_library` x3, `execute_tool spotify__show_song` x3, `execute_tool spotify__remove_song_from_playlist` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool file_system__login`, `execute_tool file_system__show_file`, `execute_tool spotify__create_playlist`, `execute_tool finish`, `Evaluator.Evaluate` |
| fd1f8fa_3 | 1457 | 50 | 26 | 1376 | 1 | `chat gemini-2.5-pro` x50, `execute_tool spotify__remove_song_from_queue` x11, `execute_tool spotify__play_music` x5, `execute_tool spotify__show_liked_songs` x4, `execute_tool spotify__show_song_queue` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool finish`, `Evaluator.Evaluate` |


## Reproducing this report

```sh
python3 reference/gen-12run-report.py /tmp/autobench/run12-kind-dev146.json v1.28 "KinD — single-node local cluster" docs/results/v1.28-2026-09-15/12run-kind.md
```

The run JSON and the mirrored artifacts it points at are produced by `reference/run-12.py`; if `/tmp` has been pruned since, re-hydrate the mirror with `reference/remirror.py` first — the generators treat a missing artifact as an empty one and will quietly emit a much shorter report.
