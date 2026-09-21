# AutoBench Service — 12 Parameterized Runs (OpenShift — ykt3 Service, ykt2 workloads (cross-cluster))

**Report generated:** 2026-09-21T00:13:06Z  
**Service version:** `v1.28`  
**Platform:** OpenShift — ykt3 Service, ykt2 workloads (cross-cluster)  
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
| **Key prefix (all 96 objects)** | `ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/` |

The key layout is `<s3.prefix>/<caller>/<iss-host-encoded>/<benchmark>/<run_id>/<artifact>`, so **benchmark is a path segment** and selects cleanly (96 objects across 12 runs):

| benchmark | runs | objects | prefix (append to the key prefix above) |
|---|---:|---:|---|
| appworld | 2 | 16 | `appworld/` |
| gsm8k | 8 | 64 | `gsm8k/` |
| tau2 | 2 | 16 | `tau2/` |

**The model in use is NOT part of the key**, so it cannot be selected by prefix. Three of the models here happen to be prefix-separable only because tau2 and appworld each used a single model; gsm8k mixes models across its runs, so its prefix necessarily returns all of them. Models present: `openai/Azure/gpt-4.1`, `openai/Azure/gpt-5-mini-2025-08-07`, `openai/aws/claude-sonnet-5`, `openai/gemini-2.5-pro`, `unknown`. To fetch the objects for one model, resolve model -> `run_id` from the summary in section 4 and use the per-run prefixes.

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
| `request_max_tokens` | The `max_tokens` the agent asked for, `null` when it asked for none (the normal case). A value of `1` marks a capability probe rather than real work: agents up to `exgentic 0.3.5.dev131` issued one per task and it was counted as an LLM call, while `dev145` replaced it with an unbilled `GET /v1/models` check that emits no span. **This run set is from after that change**: none of its 1122 `chat` spans carries `max_tokens=1`, so its `llm` counts are real calls one-for-one with no offset to subtract. The column is the unambiguous way to tell a probe from a real call, and the probe's code path still exists upstream (`strict=True` in the agent's `health.py`), so it stays. |

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
- **Model in use:** `openai/Azure/gpt-5-mini-2025-08-07`, `unknown`
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
| 1 | gsm8k | `20260915170958-e8759779` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 1 | 0/1 | 0 | 7 |
| 2 | gsm8k | `20260915174431-5ebb122e` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 10 | 0/10 | 0 | 41 |
| 3 | gsm8k | `20260915180213-d3b699be` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 50 | 0/50 | 0 | 46 |
| 4 | gsm8k | `20260915190948-9c23ba7b` | openai/Azure/gpt-4.1 | succeeded | 0.8 | 4 | 0/5 | 0 | 16 |
| 5 | gsm8k | `20260915184805-503da16b` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 5 | 0/5 | 0 | 25 |
| 6 | gsm8k | `20260915190714-f0d4c5d0` | openai/Azure/gpt-5-mini-2025-08-07, unknown | succeeded | 0.6 | 3 | 2/5 | 0 | 39 |
| 7 | gsm8k | `20260915192518-019e1de1` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 0.8 | 4 | 1/5 | 0 | 30 |
| 8 | gsm8k | `20260915194330-7dc3de88` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 5 | 0/5 | 0 | 23 |
| 9 | tau2 | `20260915185125-dd2123e6` | openai/aws/claude-sonnet-5 | succeeded | 0.9 | 9 | 0/10 | 0 | 803 |
| 10 | tau2 | `20260915174740-9e4198e7` | openai/aws/claude-sonnet-5 | succeeded | 0.75 | 15 | 0/20 | 0 | 436 |
| 11 | appworld | `20260915180500-4e84bec4` | openai/gemini-2.5-pro | succeeded | 0.0 | 0 | 2/5 | 0 | 2433 |
| 12 | appworld | `20260915171202-852eeac4` | openai/gemini-2.5-pro | succeeded | 0.0 | 0 | 3/20 | 0 | 1818 |

**Tasks with no `report.ndjson` row: #11 (2 of 5), #12 (2 of 20).** These ended before the Service wrote a per-task row — on appworld, the per-task timeout. `Err/Total` counts them (it reads `run.json`, which has one result per task unconditionally), but **§6-§7 cannot**: a missing row contributes to no median, mean or CV, so those per-task statistics describe the tasks that finished rather than the tasks that were attempted.

**Token attribution:** complete — no row lost its usage-bearing span.

**Health probe:** every task reached the model — no task was lost to the agent's per-task `GET /v1/models` check.

**✅ These runs were spaced to defeat the gateway's completion cache.** The legs do repeat tasks — gsm8k #1/#2/#3/#5/#6/#7/#8 share 10 task ids; tau2 #9/#10 share 10 task ids; appworld on `openai/gemini-2.5-pro` #11/#12 share 3 task ids — but the driver rested every (benchmark, model) prompt set for at least **900 s** before reusing it, against a measured cache TTL of ~10 min, so **no leg could replay another's completions**: 4 leg(s) were the first of their prompt set and had nothing to replay, 5 had the gap covered by other legs running in between, and 3 waited out the remainder explicitly. The gap is measured from the previous leg's *finish*, which errs safe — a shared task set is always a prefix, so the colliding prompts were sent near that leg's start and are older still. Per-call latency and output tokens are therefore independent across these legs, which was not true of earlier matrices. For the underlying mechanism: A repeated request body comes back from `ete-litellm` as the *same stored response* — identical response `id`, identical `usage` — measured with the agent bypassed, on both clusters' gateways. **The TTL is ~10 minutes**, measured by survival curve (one probe per nonce at its own age: hit at 3/5/7/9 min, miss at 11/13/15/18/21). A replay re-reports the stored token counts and its latency is a cache lookup, so per-call latency and output token counts are affected; input tokens and pass rates are not (the same prompt and the same correct answer either way). This is not an agent setting — `EXGENTIC_LITELLM_CACHING=false` is pinned and provably inert against it — and **latency is not a reliable hit detector**: one measured replay took 3.0 s, the same as a miss. Compare response `id`s. Details in `docs/exgentic-agent-bug-report-20260901.md`.

## 5. Contents of the manifest file

Every run writes a `manifest.json` at its S3 prefix — a self-describing index of the run's data objects, so a client discovers the whole run in one fetch with no S3 listing. It lists the 7 data artifacts (`run.json`, `report.ndjson`, `token_report.ndjson`, `span_report.ndjson`, `report.parquet`, `token_report.parquet`, `span_report.parquet`); the manifest does **not** list itself. Each entry carries `name`, `format`, `key`, public `url`, and `size_bytes`.

Example:

```json
{
  "run_id": "20260915170958-e8759779",
  "benchmark": "gsm8k",
  "prefix": "ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/gsm8k/20260915170958-e8759779",
  "artifacts": [
    {
      "name": "run.json",
      "format": "json",
      "key": "ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/gsm8k/20260915170958-e8759779/run.json",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/gsm8k/20260915170958-e8759779/run.json",
      "size_bytes": 467
    },
    {
      "name": "report.ndjson",
      "format": "ndjson",
      "key": "ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/gsm8k/20260915170958-e8759779/report.ndjson",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/gsm8k/20260915170958-e8759779/report.ndjson",
      "size_bytes": 1031
    },
    {
      "name": "token_report.ndjson",
      "format": "ndjson",
      "key": "ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/gsm8k/20260915170958-e8759779/token_report.ndjson",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/gsm8k/20260915170958-e8759779/token_report.ndjson",
      "size_bytes": 371
    },
    {
      "name": "span_report.ndjson",
      "format": "ndjson",
      "key": "ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/gsm8k/20260915170958-e8759779/span_report.ndjson",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/gsm8k/20260915170958-e8759779/span_report.ndjson",
      "size_bytes": 53050
    },
    {
      "name": "report.parquet",
      "format": "parquet",
      "key": "ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/gsm8k/20260915170958-e8759779/report.parquet",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/gsm8k/20260915170958-e8759779/report.parquet",
      "size_bytes": 11984
    },
    {
      "name": "token_report.parquet",
      "format": "parquet",
      "key": "ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/gsm8k/20260915170958-e8759779/token_report.parquet",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/gsm8k/20260915170958-e8759779/token_report.parquet",
      "size_bytes": 4425
    },
    {
      "name": "span_report.parquet",
      "format": "parquet",
      "key": "ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/gsm8k/20260915170958-e8759779/span_report.parquet",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/gsm8k/20260915170958-e8759779/span_report.parquet",
      "size_bytes": 10651
    }
  ]
}
```


## 6. Per-run aggregate — input & output tokens

`median` then `mean` then `CV` for each direction. median is the robust centre, mean the arithmetic average, and a mean well above the median signals right-skew (a few long tasks). `CV` is population sigma / mean, i.e. how unevenly the token cost is spread; it shares its denominator with `mean`, not `median`. CV is `—` for single-task runs.

A `⚠` in the Lost column means some of that run's rows lost their usage-bearing span (§2), so **every token figure on that row is understated** — including the median/mean/CV, which are computed over all tasks and so are dragged down by the damaged ones. Do not quote them as the run's cost.

| # | Benchmark | Model | Tasks | Lost | LLM calls | Input | Output | Total | IN median | IN mean | IN CV | OUT median | OUT mean | OUT CV |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 1 | 0 | 1 | 320 | 150 | 470 | 320 | 320 | — | 150 | 150 | — |
| 2 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 10 | 0 | 10 | 3166 | 1885 | 5051 | 312 | 317 | 0.08 | 86 | 188 | 0.86 |
| 3 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 50 | 0 | 50 | 15684 | 9294 | 24978 | 310 | 314 | 0.06 | 150 | 186 | 0.70 |
| 4 | gsm8k | openai/Azure/gpt-4.1 | 5 | 0 | 14 | 3874 | 284 | 4158 | 807 | 775 | 0.39 | 50 | 57 | 0.55 |
| 5 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 5 | 0 | 5 | 1564 | 687 | 2251 | 306 | 313 | 0.09 | 86 | 137 | 0.75 |
| 6 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07, unknown | 5 | 0 | 4 | 1258 | 344 | 1602 | 291 | 252 | 0.51 | 86 | 69 | 0.50 |
| 7 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 5 | 0 | 5 | 1564 | 1839 | 3403 | 306 | 313 | 0.09 | 407 | 368 | 0.52 |
| 8 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 5 | 0 | 5 | 1564 | 623 | 2187 | 306 | 313 | 0.09 | 86 | 125 | 0.62 |
| 9 | tau2 | openai/aws/claude-sonnet-5 | 10 | 0 | 118 | 988110 | 21045 | 1009155 | 94026 | 98811 | 0.12 | 2104 | 2104 | 0.10 |
| 10 | tau2 | openai/aws/claude-sonnet-5 | 20 | 0 | 220 | 1703460 | 40797 | 1744257 | 89010 | 85173 | 0.17 | 1904 | 2040 | 0.29 |
| 11 | appworld | openai/gemini-2.5-pro | 3 | 0 | 128 | 1387383 | 105279 | 1492662 | 416540 | 462461 | 0.15 | 32764 | 35093 | 0.13 |
| 12 | appworld | openai/gemini-2.5-pro | 18 | 0 | 562 | 5211584 | 500426 | 5712010 | 220488 | 289532 | 0.65 | 22355 | 27801 | 0.50 |

## 7. Per-task detail

`model` is repeated on every row deliberately: token counts are only comparable within a model, and the runs do not all use the same one (#4 swaps the gsm8k model, so its ~815 input tokens per task are not comparable with the ~320 of #1–3/#5–8 on the same tasks).

`tool` is shown next to `llm` because their relationship is the tell for a damaged row: each tool call needs a preceding model turn, so `llm=1` beside `tool=11` cannot be real work — it is a lost span (§2), flagged `⚠`.

### Run #1 — gsm8k  ·  `20260915170958-e8759779`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 150 | 470 | 1 | 1 | True |  |

### Run #2 — gsm8k  ·  `20260915174431-5ebb122e`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 150 | 470 | 1 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 86 | 369 | 1 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 343 | 649 | 1 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 86 | 377 | 1 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 86 | 450 | 1 | 1 | True |  |
| 5 | openai/Azure/gpt-5-mini-2025-08-07 | 309 | 86 | 395 | 1 | 1 | True |  |
| 6 | openai/Azure/gpt-5-mini-2025-08-07 | 298 | 86 | 384 | 1 | 1 | True |  |
| 7 | openai/Azure/gpt-5-mini-2025-08-07 | 323 | 598 | 921 | 1 | 1 | True |  |
| 8 | openai/Azure/gpt-5-mini-2025-08-07 | 358 | 278 | 636 | 1 | 1 | True |  |
| 9 | openai/Azure/gpt-5-mini-2025-08-07 | 314 | 86 | 400 | 1 | 1 | True |  |

### Run #3 — gsm8k  ·  `20260915180213-d3b699be`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 86 | 406 | 1 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 86 | 369 | 1 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 279 | 585 | 1 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 86 | 377 | 1 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 86 | 450 | 1 | 1 | True |  |
| 5 | openai/Azure/gpt-5-mini-2025-08-07 | 309 | 150 | 459 | 1 | 1 | True |  |
| 6 | openai/Azure/gpt-5-mini-2025-08-07 | 298 | 342 | 640 | 1 | 1 | True |  |
| 7 | openai/Azure/gpt-5-mini-2025-08-07 | 323 | 214 | 537 | 1 | 1 | True |  |
| 8 | openai/Azure/gpt-5-mini-2025-08-07 | 358 | 278 | 636 | 1 | 1 | True |  |
| 9 | openai/Azure/gpt-5-mini-2025-08-07 | 314 | 86 | 400 | 1 | 1 | True |  |
| 10 | openai/Azure/gpt-5-mini-2025-08-07 | 315 | 86 | 401 | 1 | 1 | True |  |
| 11 | openai/Azure/gpt-5-mini-2025-08-07 | 316 | 86 | 402 | 1 | 1 | True |  |
| 12 | openai/Azure/gpt-5-mini-2025-08-07 | 322 | 278 | 600 | 1 | 1 | True |  |
| 13 | openai/Azure/gpt-5-mini-2025-08-07 | 315 | 214 | 529 | 1 | 1 | True |  |
| 14 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 150 | 456 | 1 | 1 | True |  |
| 15 | openai/Azure/gpt-5-mini-2025-08-07 | 347 | 150 | 497 | 1 | 1 | True |  |
| 16 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 214 | 520 | 1 | 1 | True |  |
| 17 | openai/Azure/gpt-5-mini-2025-08-07 | 309 | 87 | 396 | 1 | 1 | True |  |
| 18 | openai/Azure/gpt-5-mini-2025-08-07 | 284 | 534 | 818 | 1 | 1 | True |  |
| 19 | openai/Azure/gpt-5-mini-2025-08-07 | 321 | 150 | 471 | 1 | 1 | True |  |
| 20 | openai/Azure/gpt-5-mini-2025-08-07 | 317 | 278 | 595 | 1 | 1 | True |  |
| 21 | openai/Azure/gpt-5-mini-2025-08-07 | 301 | 598 | 899 | 1 | 1 | True |  |
| 22 | openai/Azure/gpt-5-mini-2025-08-07 | 311 | 86 | 397 | 1 | 1 | True |  |
| 23 | openai/Azure/gpt-5-mini-2025-08-07 | 293 | 86 | 379 | 1 | 1 | True |  |
| 24 | openai/Azure/gpt-5-mini-2025-08-07 | 292 | 86 | 378 | 1 | 1 | True |  |
| 25 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 406 | 726 | 1 | 1 | True |  |
| 26 | openai/Azure/gpt-5-mini-2025-08-07 | 321 | 86 | 407 | 1 | 1 | True |  |
| 27 | openai/Azure/gpt-5-mini-2025-08-07 | 310 | 150 | 460 | 1 | 1 | True |  |
| 28 | openai/Azure/gpt-5-mini-2025-08-07 | 304 | 86 | 390 | 1 | 1 | True |  |
| 29 | openai/Azure/gpt-5-mini-2025-08-07 | 324 | 534 | 858 | 1 | 1 | True |  |
| 30 | openai/Azure/gpt-5-mini-2025-08-07 | 292 | 86 | 378 | 1 | 1 | True |  |
| 31 | openai/Azure/gpt-5-mini-2025-08-07 | 317 | 150 | 467 | 1 | 1 | True |  |
| 32 | openai/Azure/gpt-5-mini-2025-08-07 | 297 | 86 | 383 | 1 | 1 | True |  |
| 33 | openai/Azure/gpt-5-mini-2025-08-07 | 285 | 406 | 691 | 1 | 1 | True |  |
| 34 | openai/Azure/gpt-5-mini-2025-08-07 | 297 | 86 | 383 | 1 | 1 | True |  |
| 35 | openai/Azure/gpt-5-mini-2025-08-07 | 305 | 86 | 391 | 1 | 1 | True |  |
| 36 | openai/Azure/gpt-5-mini-2025-08-07 | 297 | 86 | 383 | 1 | 1 | True |  |
| 37 | openai/Azure/gpt-5-mini-2025-08-07 | 315 | 214 | 529 | 1 | 1 | True |  |
| 38 | openai/Azure/gpt-5-mini-2025-08-07 | 300 | 214 | 514 | 1 | 1 | True |  |
| 39 | openai/Azure/gpt-5-mini-2025-08-07 | 329 | 214 | 543 | 1 | 1 | True |  |
| 40 | openai/Azure/gpt-5-mini-2025-08-07 | 308 | 214 | 522 | 1 | 1 | True |  |
| 41 | openai/Azure/gpt-5-mini-2025-08-07 | 379 | 86 | 465 | 1 | 1 | True |  |
| 42 | openai/Azure/gpt-5-mini-2025-08-07 | 336 | 406 | 742 | 1 | 1 | True |  |
| 43 | openai/Azure/gpt-5-mini-2025-08-07 | 310 | 150 | 460 | 1 | 1 | True |  |
| 44 | openai/Azure/gpt-5-mini-2025-08-07 | 326 | 150 | 476 | 1 | 1 | True |  |
| 45 | openai/Azure/gpt-5-mini-2025-08-07 | 350 | 214 | 564 | 1 | 1 | True |  |
| 46 | openai/Azure/gpt-5-mini-2025-08-07 | 346 | 86 | 432 | 1 | 1 | True |  |
| 47 | openai/Azure/gpt-5-mini-2025-08-07 | 303 | 150 | 453 | 1 | 1 | True |  |
| 48 | openai/Azure/gpt-5-mini-2025-08-07 | 294 | 86 | 380 | 1 | 1 | True |  |
| 49 | openai/Azure/gpt-5-mini-2025-08-07 | 298 | 86 | 384 | 1 | 1 | True |  |

### Run #4 — gsm8k  ·  `20260915190948-9c23ba7b`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-4.1 | 807 | 50 | 857 | 3 | 3 | True |  |
| 1 | openai/Azure/gpt-4.1 | 438 | 34 | 472 | 2 | 2 | True |  |
| 2 | openai/Azure/gpt-4.1 | 1242 | 117 | 1359 | 4 | 4 | False |  |
| 3 | openai/Azure/gpt-4.1 | 451 | 33 | 484 | 2 | 2 | True |  |
| 4 | openai/Azure/gpt-4.1 | 936 | 50 | 986 | 3 | 3 | True |  |

### Run #5 — gsm8k  ·  `20260915184805-503da16b`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 86 | 406 | 1 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 86 | 369 | 1 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 343 | 649 | 1 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 86 | 377 | 1 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 86 | 450 | 1 | 1 | True |  |

### Run #6 — gsm8k  ·  `20260915190714-f0d4c5d0`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 86 | 406 | 1 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 86 | 369 | 1 | 1 | None |  |
| 2 | unknown | 0 | 0 | 0 | 0 | 0 | None |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 86 | 377 | 1 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 86 | 450 | 1 | 1 | True |  |

### Run #7 — gsm8k  ·  `20260915192518-019e1de1`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 214 | 534 | 1 | 1 | None |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 598 | 881 | 1 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 407 | 713 | 1 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 534 | 825 | 1 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 86 | 450 | 1 | 1 | True |  |

### Run #8 — gsm8k  ·  `20260915194330-7dc3de88`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 86 | 406 | 1 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 86 | 369 | 1 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 279 | 585 | 1 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 86 | 377 | 1 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 86 | 450 | 1 | 1 | True |  |

### Run #9 — tau2  ·  `20260915185125-dd2123e6`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/aws/claude-sonnet-5 | 92670 | 2037 | 94707 | 12 | 12 | True |  |
| 1 | openai/aws/claude-sonnet-5 | 92220 | 2014 | 94234 | 12 | 12 | True |  |
| 2 | openai/aws/claude-sonnet-5 | 124201 | 2104 | 126305 | 14 | 14 | True |  |
| 3 | openai/aws/claude-sonnet-5 | 98545 | 1711 | 100256 | 12 | 12 | True |  |
| 4 | openai/aws/claude-sonnet-5 | 81364 | 2378 | 83742 | 10 | 10 | True |  |
| 5 | openai/aws/claude-sonnet-5 | 118981 | 2105 | 121086 | 13 | 13 | False |  |
| 6 | openai/aws/claude-sonnet-5 | 99188 | 2108 | 101296 | 12 | 12 | True |  |
| 7 | openai/aws/claude-sonnet-5 | 92890 | 1875 | 94765 | 11 | 11 | True |  |
| 8 | openai/aws/claude-sonnet-5 | 93847 | 2271 | 96118 | 11 | 11 | True |  |
| 9 | openai/aws/claude-sonnet-5 | 94204 | 2442 | 96646 | 11 | 11 | True |  |

### Run #10 — tau2  ·  `20260915174740-9e4198e7`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/aws/claude-sonnet-5 | 70761 | 1823 | 72584 | 10 | 10 | True |  |
| 1 | openai/aws/claude-sonnet-5 | 104949 | 2787 | 107736 | 13 | 13 | False |  |
| 2 | openai/aws/claude-sonnet-5 | 88783 | 1669 | 90452 | 10 | 10 | False |  |
| 3 | openai/aws/claude-sonnet-5 | 98929 | 1865 | 100794 | 12 | 12 | True |  |
| 4 | openai/aws/claude-sonnet-5 | 85356 | 2543 | 87899 | 10 | 10 | False |  |
| 5 | openai/aws/claude-sonnet-5 | 106226 | 2389 | 108615 | 12 | 12 | True |  |
| 6 | openai/aws/claude-sonnet-5 | 93584 | 2752 | 96336 | 11 | 11 | True |  |
| 7 | openai/aws/claude-sonnet-5 | 93509 | 1976 | 95485 | 11 | 11 | True |  |
| 8 | openai/aws/claude-sonnet-5 | 93956 | 2408 | 96364 | 11 | 11 | True |  |
| 9 | openai/aws/claude-sonnet-5 | 81118 | 2898 | 84016 | 10 | 10 | True |  |
| 10 | openai/aws/claude-sonnet-5 | 48170 | 1293 | 49463 | 8 | 8 | True |  |
| 11 | openai/aws/claude-sonnet-5 | 75418 | 1854 | 77272 | 11 | 11 | True |  |
| 12 | openai/aws/claude-sonnet-5 | 83171 | 1730 | 84901 | 12 | 12 | False |  |
| 13 | openai/aws/claude-sonnet-5 | 89236 | 1967 | 91203 | 13 | 13 | True |  |
| 14 | openai/aws/claude-sonnet-5 | 73300 | 1393 | 74693 | 11 | 11 | True |  |
| 15 | openai/aws/claude-sonnet-5 | 90112 | 1694 | 91806 | 11 | 11 | True |  |
| 16 | openai/aws/claude-sonnet-5 | 68144 | 1944 | 70088 | 9 | 9 | True |  |
| 17 | openai/aws/claude-sonnet-5 | 61427 | 846 | 62273 | 10 | 10 | True |  |
| 18 | openai/aws/claude-sonnet-5 | 92006 | 1651 | 93657 | 13 | 13 | False |  |
| 19 | openai/aws/claude-sonnet-5 | 105305 | 3315 | 108620 | 12 | 12 | True |  |

### Run #11 — appworld  ·  `20260915180500-4e84bec4`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 3d9a636_2 | openai/gemini-2.5-pro | 408657 | 31221 | 439878 | 30 | 15 | False |  |
| 3d9a636_3 | openai/gemini-2.5-pro | 416540 | 32764 | 449304 | 44 | 22 | False |  |
| fd1f8fa_1 | openai/gemini-2.5-pro | 562186 | 41294 | 603480 | 54 | 27 | False |  |

### Run #12 — appworld  ·  `20260915171202-852eeac4`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 3d9a636_1 | openai/gemini-2.5-pro | 193704 | 21673 | 215377 | 24 | 12 | False |  |
| 3d9a636_2 | openai/gemini-2.5-pro | 183509 | 18434 | 201943 | 24 | 12 | False |  |
| 3d9a636_3 | openai/gemini-2.5-pro | 755257 | 49108 | 804365 | 60 | 30 | False |  |
| 21abae1_1 | openai/gemini-2.5-pro | 67969 | 11293 | 79262 | 10 | 5 | False |  |
| 21abae1_2 | openai/gemini-2.5-pro | 64918 | 9449 | 74367 | 10 | 5 | False |  |
| 21abae1_3 | openai/gemini-2.5-pro | 75794 | 11498 | 87292 | 12 | 6 | False |  |
| 29a7b7e_1 | openai/gemini-2.5-pro | 524219 | 55725 | 579944 | 54 | 27 | False |  |
| 29a7b7e_3 | openai/gemini-2.5-pro | 136924 | 23341 | 160265 | 18 | 9 | False |  |
| 325d6ec_1 | openai/gemini-2.5-pro | 175499 | 17964 | 193463 | 26 | 13 | False |  |
| 325d6ec_2 | openai/gemini-2.5-pro | 136303 | 13922 | 150225 | 20 | 10 | False |  |
| 325d6ec_3 | openai/gemini-2.5-pro | 284805 | 21902 | 306707 | 38 | 19 | False |  |
| 634f342_1 | openai/gemini-2.5-pro | 442137 | 45648 | 487785 | 48 | 24 | False |  |
| 634f342_3 | openai/gemini-2.5-pro | 247273 | 22147 | 269420 | 26 | 13 | False |  |
| 8749218_1 | openai/gemini-2.5-pro | 398165 | 43057 | 441222 | 46 | 23 | False |  |
| 8749218_2 | openai/gemini-2.5-pro | 153121 | 22563 | 175684 | 20 | 9 | None |  |
| fd1f8fa_1 | openai/gemini-2.5-pro | 450057 | 37820 | 487877 | 44 | 22 | False |  |
| fd1f8fa_2 | openai/gemini-2.5-pro | 428596 | 33120 | 461716 | 36 | 18 | False |  |
| fd1f8fa_3 | openai/gemini-2.5-pro | 493334 | 41762 | 535096 | 46 | 23 | False |  |


## 8. Per-task span inventory

Which spans each task actually invoked, by name. §6 and §7 report *counts*; this is what they were counted from, read straight out of each run's `span_report.ndjson`.

Read the **chat** and **tool** columns together. There is no fixed healthy chat count — a one-shot gsm8k task legitimately shows a single `chat` span, and a multi-turn tau2 task shows many. What is not possible is **`chat` ≤ 1 alongside `tool` ≥ 2**: every tool call needs a model turn to request it, so a task cannot invoke two tools off one chat span. That shape means a usage-bearing span was dropped, and this section flags it. Reading it off *counts* rather than token values is what makes it catchable on every model, including ones whose dropped span would still have reported plausible non-zero usage.

⚠ **The `chat` and `tool` columns below are raw span totals; the ⚠ flag is computed on the `counted` subset only.** Do not apply the rule by hand to these columns. A healthy gsm8k task emits two `execute_tool` spans — `initial_observation` and `submit` — of which only `submit` is counted, so its raw pair is `1`/`2` and would trip the test while its §7 pair is the innocent `1`/`1`. Filtering to `counted` is what makes this section agree with §7.

Up to `exgentic 0.3.5.dev131` each task also issued a `max_tokens=1` capability probe, so a bare `chat == 1` used to be the damage signal and **at least 2** was healthy. The probe was replaced in `dev145` by an unbilled `GET /v1/models` check that emits no span — do not resurrect that rule, and do not compare chat counts across runs that straddle the change. **This run set is from after that change**: none of its 1122 `chat` spans carries `max_tokens=1`, so its `llm` counts are real calls one-for-one with no offset to subtract.

`not counted` are spans the aggregator cannot see: it only folds a chat/tool span into `llm_count`/`tool_count` when its parent is the `invoke_agent` span, so anything nested deeper is real work missing from the totals. A non-zero figure there is not a bug by itself — it is the known blind spot, now measurable.

The **names** column lists only the spans the Service names (`root`/`phase`) and those the agent names (`agent`/`chat`/`tool`). The `other` column counts the rest — A2A/HTTP framework internals inside the agent pod, such as `EventQueue.dequeue_event` or the ASGI `POST / http send`, which dominate the raw count (a single gsm8k task emits ~98 spans, ~90 of them framework noise) and would swamp this table. They are all present in `span_report.ndjson`; this section is the readable summary, not the full tree.

### Run #1 — gsm8k  ·  `20260915170958-e8759779`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 101 | 1 | 2 | 93 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #2 — gsm8k  ·  `20260915174431-5ebb122e`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 99 | 1 | 2 | 91 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 94 | 1 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 97 | 1 | 2 | 89 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 95 | 1 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 94 | 1 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 5 | 93 | 1 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 6 | 101 | 1 | 2 | 93 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 7 | 105 | 1 | 2 | 97 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 8 | 97 | 1 | 2 | 89 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 9 | 95 | 1 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #3 — gsm8k  ·  `20260915180213-d3b699be`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 97 | 1 | 2 | 89 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 96 | 1 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 100 | 1 | 2 | 92 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 99 | 1 | 2 | 91 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 94 | 1 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 5 | 95 | 1 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 6 | 99 | 1 | 2 | 91 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 7 | 97 | 1 | 2 | 89 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 8 | 97 | 1 | 2 | 89 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 9 | 94 | 1 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 10 | 93 | 1 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 11 | 93 | 1 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 12 | 97 | 1 | 2 | 89 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 13 | 95 | 1 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 14 | 94 | 1 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 15 | 95 | 1 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 16 | 95 | 1 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 17 | 93 | 1 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 18 | 101 | 1 | 2 | 93 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 19 | 94 | 1 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 20 | 104 | 1 | 2 | 96 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 21 | 101 | 1 | 2 | 93 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 22 | 93 | 1 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 23 | 95 | 1 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 24 | 94 | 1 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 25 | 98 | 1 | 2 | 90 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 26 | 94 | 1 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 27 | 94 | 1 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 28 | 93 | 1 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 29 | 99 | 1 | 2 | 91 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 30 | 94 | 1 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 31 | 93 | 1 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 32 | 94 | 1 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 33 | 98 | 1 | 2 | 90 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 34 | 93 | 1 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 35 | 94 | 1 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 36 | 94 | 1 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 37 | 95 | 1 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 38 | 95 | 1 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 39 | 95 | 1 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 40 | 96 | 1 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 41 | 94 | 1 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 42 | 98 | 1 | 2 | 90 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 43 | 95 | 1 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 44 | 97 | 1 | 2 | 89 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 45 | 97 | 1 | 2 | 89 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 46 | 101 | 1 | 2 | 93 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 47 | 100 | 1 | 2 | 92 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 48 | 98 | 1 | 2 | 90 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 49 | 97 | 1 | 2 | 89 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #4 — gsm8k  ·  `20260915190948-9c23ba7b`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 139 | 3 | 4 | 127 | 1 | `chat Azure/gpt-4.1` x3, `execute_tool calculate_expression` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 120 | 2 | 3 | 110 | 1 | `chat Azure/gpt-4.1` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool calculate_expression`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 156 | 4 | 5 | 142 | 1 | `chat Azure/gpt-4.1` x4, `execute_tool calculate_expression` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 117 | 2 | 3 | 107 | 1 | `chat Azure/gpt-4.1` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool calculate_expression`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 125 | 3 | 4 | 113 | 1 | `chat Azure/gpt-4.1` x3, `execute_tool calculate_expression` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #5 — gsm8k  ·  `20260915184805-503da16b`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 106 | 1 | 2 | 98 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 101 | 1 | 2 | 93 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 106 | 1 | 2 | 98 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 95 | 1 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 100 | 1 | 2 | 92 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #6 — gsm8k  ·  `20260915190714-f0d4c5d0`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 106 | 1 | 2 | 98 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 99 | 1 | 2 | 92 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit` |
| 2 | 111 | 0 | 1 | 107 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `execute_tool initial_observation` |
| 3 | 99 | 1 | 2 | 91 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 96 | 1 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #7 — gsm8k  ·  `20260915192518-019e1de1`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 114 | 1 | 2 | 107 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit` |
| 1 | 105 | 1 | 2 | 97 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 111 | 1 | 2 | 103 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 109 | 1 | 2 | 101 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 101 | 1 | 2 | 93 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #8 — gsm8k  ·  `20260915194330-7dc3de88`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 104 | 1 | 2 | 96 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 109 | 1 | 2 | 101 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 107 | 1 | 2 | 99 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 100 | 1 | 2 | 92 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 96 | 1 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #9 — tau2  ·  `20260915185125-dd2123e6`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 369 | 12 | 13 | 339 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x6, `execute_tool get_order_details` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 1 | 363 | 12 | 13 | 333 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x6, `execute_tool get_order_details` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 2 | 393 | 14 | 15 | 359 | 1 | `chat aws/claude-sonnet-5` x14, `execute_tool message` x8, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 3 | 373 | 12 | 13 | 343 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 4 | 333 | 10 | 11 | 307 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 5 | 407 | 13 | 14 | 375 | 1 | `chat aws/claude-sonnet-5` x13, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 6 | 404 | 12 | 13 | 374 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 7 | 327 | 11 | 12 | 299 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 8 | 347 | 11 | 12 | 319 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 9 | 353 | 11 | 12 | 325 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |

### Run #10 — tau2  ·  `20260915174740-9e4198e7`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 0 | 307 | 10 | 11 | 281 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x4, `execute_tool get_order_details` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 1 | 381 | 13 | 14 | 349 | 1 | `chat aws/claude-sonnet-5` x13, `execute_tool message` x6, `execute_tool get_order_details` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `execute_tool transfer_to_human_agents`, `Evaluator.Evaluate` |
| 2 | 335 | 10 | 11 | 309 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 3 | 380 | 12 | 13 | 350 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 4 | 341 | 10 | 11 | 315 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool get_order_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 5 | 362 | 12 | 13 | 332 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 6 | 375 | 11 | 12 | 347 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 7 | 343 | 11 | 12 | 315 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 8 | 394 | 11 | 12 | 366 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 9 | 379 | 10 | 11 | 353 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 10 | 334 | 8 | 9 | 312 | 1 | `chat aws/claude-sonnet-5` x8, `execute_tool message` x4, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool transfer_to_human_agents`, `Evaluator.Evaluate` |
| 11 | 321 | 11 | 12 | 293 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 12 | 349 | 12 | 13 | 319 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x8, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 13 | 382 | 13 | 14 | 350 | 1 | `chat aws/claude-sonnet-5` x13, `execute_tool message` x8, `execute_tool return_delivered_order_items` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `Evaluator.Evaluate` |
| 14 | 345 | 11 | 12 | 317 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 15 | 332 | 11 | 12 | 304 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 16 | 385 | 9 | 10 | 361 | 1 | `chat aws/claude-sonnet-5` x9, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool cancel_pending_order`, `Evaluator.Evaluate` |
| 17 | 284 | 10 | 11 | 258 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x5, `execute_tool get_order_details` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool modify_pending_order_address`, `Evaluator.Evaluate` |
| 18 | 370 | 13 | 14 | 338 | 1 | `chat aws/claude-sonnet-5` x13, `execute_tool message` x9, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 19 | 389 | 12 | 13 | 359 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool return_delivered_order_items`, `execute_tool transfer_to_human_agents`, `Evaluator.Evaluate` |

### Run #11 — appworld  ·  `20260915180500-4e84bec4`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 3d9a636_2 | 1110 | 30 | 16 | 1059 | 1 | `chat gemini-2.5-pro` x30, `execute_tool venmo__search_friends` x4, `execute_tool phone__search_contacts` x3, `execute_tool venmo__add_friend` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool phone__show_contact_relationships`, `execute_tool phone__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| 3d9a636_3 | 1256 | 44 | 23 | 1184 | 1 | `chat gemini-2.5-pro` x44, `execute_tool venmo__add_friend` x5, `execute_tool venmo__remove_friend` x5, `execute_tool phone__search_contacts` x3, `execute_tool venmo__search_friends` x3, `execute_tool phone__login` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool phone__show_contact_relationships`, `execute_tool finish`, `Evaluator.Evaluate` |
| fd1f8fa_1 | 1309 | 54 | 28 | 1222 | 1 | `chat gemini-2.5-pro` x54, `execute_tool spotify__remove_song_from_queue` x11, `execute_tool supervisor__show_account_passwords` x4, `execute_tool spotify__login` x4, `execute_tool spotify__show_liked_songs` x2, `execute_tool spotify__show_song_queue` x2, `execute_tool spotify__play_music` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool spotify__next_song`, `execute_tool finish`, `Evaluator.Evaluate` |

### Run #12 — appworld  ·  `20260915171202-852eeac4`

| task | spans | chat | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---|
| 3d9a636_1 | 743 | 24 | 13 | 701 | 1 | `chat gemini-2.5-pro` x24, `execute_tool phone__search_contacts` x3, `execute_tool phone__login` x2, `execute_tool venmo__search_friends` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool phone__show_contact_relationships`, `execute_tool venmo__add_friend`, `execute_tool venmo__remove_friend`, `execute_tool finish`, `Evaluator.Evaluate` |
| 3d9a636_2 | 678 | 24 | 13 | 636 | 1 | `chat gemini-2.5-pro` x24, `execute_tool venmo__search_friends` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool supervisor__show_profile`, `execute_tool venmo__login`, `execute_tool phone__login`, `execute_tool phone__show_contact_relationships`, `execute_tool phone__search_contacts`, `execute_tool venmo__remove_friend`, `execute_tool venmo__add_friend`, `execute_tool finish`, `Evaluator.Evaluate` |
| 3d9a636_3 | 1547 | 60 | 31 | 1451 | 1 | `chat gemini-2.5-pro` x60, `execute_tool venmo__add_friend` x14, `execute_tool phone__search_contacts` x10, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool supervisor__show_profile`, `execute_tool phone__login`, `execute_tool phone__show_contact_relationships`, `execute_tool venmo__search_friends`, `execute_tool finish`, `Evaluator.Evaluate` |
| 21abae1_1 | 370 | 10 | 6 | 349 | 1 | `chat gemini-2.5-pro` x10, `execute_tool venmo__show_transactions` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| 21abae1_2 | 350 | 10 | 6 | 329 | 1 | `chat gemini-2.5-pro` x10, `execute_tool venmo__show_transactions` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| 21abae1_3 | 391 | 12 | 7 | 367 | 1 | `chat gemini-2.5-pro` x12, `execute_tool venmo__show_transactions` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| 29a7b7e_1 | 1532 | 54 | 28 | 1445 | 1 | `chat gemini-2.5-pro` x54, `execute_tool file_system__move_file` x20, `execute_tool file_system__show_directory` x2, `execute_tool file_system__create_directory` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool file_system__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| 29a7b7e_3 | 764 | 18 | 10 | 731 | 1 | `chat gemini-2.5-pro` x18, `execute_tool file_system__move_file` x3, `execute_tool file_system__create_directory` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool file_system__login`, `execute_tool file_system__show_directory`, `execute_tool finish`, `Evaluator.Evaluate` |
| 325d6ec_1 | 669 | 26 | 14 | 624 | 1 | `chat gemini-2.5-pro` x26, `execute_tool spotify__previous_song` x5, `execute_tool spotify__show_song_privates` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| 325d6ec_2 | 516 | 20 | 11 | 480 | 1 | `chat gemini-2.5-pro` x20, `execute_tool spotify__next_song` x4, `execute_tool spotify__show_downloaded_songs` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool spotify__show_current_song`, `execute_tool finish`, `Evaluator.Evaluate` |
| 325d6ec_3 | 887 | 38 | 20 | 824 | 1 | `chat gemini-2.5-pro` x38, `execute_tool spotify__show_current_song` x6, `execute_tool spotify__show_song_privates` x5, `execute_tool spotify__next_song` x4, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool supervisor__show_profile`, `execute_tool spotify__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| 634f342_1 | 1484 | 48 | 25 | 1406 | 1 | `chat gemini-2.5-pro` x48, `execute_tool spotify__show_song` x12, `execute_tool spotify__add_song_to_playlist` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool file_system__show_file`, `execute_tool spotify__create_playlist`, `execute_tool spotify__show_playlist_library`, `execute_tool spotify__remove_song_from_playlist`, `execute_tool finish`, `Evaluator.Evaluate` |
| 634f342_3 | 920 | 26 | 14 | 875 | 1 | `chat gemini-2.5-pro` x26, `execute_tool spotify__show_playlist_library` x3, `execute_tool spotify__show_song` x3, `execute_tool spotify__remove_song_from_playlist` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool file_system__login`, `execute_tool file_system__show_file`, `execute_tool finish`, `Evaluator.Evaluate` |
| 8749218_1 | 1358 | 46 | 24 | 1283 | 1 | `chat gemini-2.5-pro` x46, `execute_tool gmail__show_inbox_threads` x11, `execute_tool spotify__login` x2, `execute_tool gmail__login` x2, `execute_tool phone__show_text_message` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool spotify__clear_song_queue`, `execute_tool supervisor__show_account_passwords`, `execute_tool supervisor__show_profile`, `execute_tool spotify__search_users`, `execute_tool spotify__send_password_reset_code`, `execute_tool finish`, `Evaluator.Evaluate` |
| 8749218_2 | 703 | 20 | 10 | 669 | 1 | `chat gemini-2.5-pro` x20, `execute_tool spotify__add_to_queue` x3, `execute_tool spotify__show_recommendations` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool spotify__clear_song_queue`, `execute_tool spotify__create_playlist` |
| fd1f8fa_1 | 1148 | 44 | 23 | 1076 | 1 | `chat gemini-2.5-pro` x44, `execute_tool spotify__remove_song_from_queue` x11, `execute_tool spotify__play_music` x4, `execute_tool spotify__show_liked_songs` x2, `execute_tool spotify__show_song_queue` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| fd1f8fa_2 | 1083 | 36 | 19 | 1023 | 1 | `chat gemini-2.5-pro` x36, `execute_tool spotify__play_music` x7, `execute_tool spotify__show_song_queue` x3, `execute_tool spotify__show_liked_songs` x3, `execute_tool spotify__remove_song_from_queue` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| fd1f8fa_3 | 1315 | 46 | 24 | 1240 | 1 | `chat gemini-2.5-pro` x46, `execute_tool spotify__remove_song_from_queue` x11, `execute_tool spotify__play_music` x5, `execute_tool spotify__show_liked_songs` x2, `execute_tool spotify__show_song_queue` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool finish`, `Evaluator.Evaluate` |


## Reproducing this report

```sh
python3 reference/gen-12run-report.py /tmp/autobench/run12-ocp-dev146.json v1.28 "OpenShift — ykt3 Service, ykt2 workloads (cross-cluster)" docs/results/v1.28-2026-09-15/12run-ocp.md
```

The run JSON and the mirrored artifacts it points at are produced by `reference/run-12.py`; if `/tmp` has been pruned since, re-hydrate the mirror with `reference/remirror.py` first — the generators treat a missing artifact as an empty one and will quietly emit a much shorter report.
