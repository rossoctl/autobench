# AutoBench Service — 12 Parameterized Runs (OpenShift — ykt3, single-cluster Helm install, agent runner direct (ETE-ext gateway))

**Report generated:** 2026-10-12T22:26:48Z  
**Service version:** `v1.36`  
**Platform:** OpenShift — ykt3, single-cluster Helm install, agent runner direct (ETE-ext gateway)  
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
| **Key prefix (all 96 objects)** | `ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/` |

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
| `request_max_tokens` | The `max_tokens` the agent asked for, `null` when it asked for none (the normal case). A value of `1` marks a capability probe rather than real work: agents up to `exgentic 0.3.5.dev131` issued one per task and it was counted as an LLM call, while `dev145` replaced it with an unbilled `GET /v1/models` check that emits no span. **This run set is from after that change**: none of its 1100 `chat` spans carries `max_tokens=1`, so its `llm` counts are real calls one-for-one with no offset to subtract. The column is the unambiguous way to tell a probe from a real call, and the probe's code path still exists upstream (`strict=True` in the agent's `health.py`), so it stays. |

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
| 1 | gsm8k | `20261012195705-8bd41896` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 1 | 0/1 | 0 | 7 |
| 2 | gsm8k | `20261012202618-c8ac5eef` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 10 | 0/10 | 0 | 64 |
| 3 | gsm8k | `20261012204439-5426e547` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 50 | 0/50 | 0 | 80 |
| 4 | gsm8k | `20261012215030-7db4c5ca` | openai/Azure/gpt-4.1 | succeeded | 0.8 | 4 | 0/5 | 0 | 21 |
| 5 | gsm8k | `20261012212811-6db1a975` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 5 | 0/5 | 0 | 9 |
| 6 | gsm8k | `20261012214834-196b5858` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 5 | 0/5 | 0 | 16 |
| 7 | gsm8k | `20261012220600-d5b294c3` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 0.8 | 4 | 1/5 | 0 | 16 |
| 8 | gsm8k | `20261012222326-aeeeaf38` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 5 | 0/5 | 0 | 14 |
| 9 | tau2 | `20261012213048-2f110608` | openai/aws/claude-sonnet-5 | succeeded | 0.7 | 7 | 0/10 | 0 | 933 |
| 10 | tau2 | `20261012203006-51e48eac` | openai/aws/claude-sonnet-5 | succeeded | 0.9 | 18 | 0/20 | 0 | 765 |
| 11 | appworld | `20261012204756-7f2b30d4` | openai/gemini-2.5-pro | succeeded | 0.0 | 0 | 1/5 | 0 | 2280 |
| 12 | appworld | `20261012195911-abd3bc82` | openai/gemini-2.5-pro | succeeded | 0.0 | 0 | 3/20 | 0 | 1513 |

**Tasks with no `report.ndjson` row: #11 (1 of 5).** These ended before the Service wrote a per-task row — on appworld, the per-task timeout. `Err/Total` counts them (it reads `run.json`, which has one result per task unconditionally), but **§6-§7 cannot**: a missing row contributes to no median, mean or CV, so those per-task statistics describe the tasks that finished rather than the tasks that were attempted.

**Token attribution:** complete — no row lost its usage-bearing span.

**Health probe:** every task reached the model — no task was lost to the agent's per-task `GET /v1/models` check.

**✅ These runs were spaced to defeat the gateway's completion cache.** The legs do repeat tasks — gsm8k #1/#2/#3/#5/#6/#7/#8 share 10 task ids; tau2 #9/#10 share 10 task ids; appworld on `openai/gemini-2.5-pro` #11/#12 share 4 task ids — but the driver rested every (benchmark, model) prompt set for at least **900 s** before reusing it, against a measured cache TTL of ~10 min, so **no leg could replay another's completions**: 4 leg(s) were the first of their prompt set and had nothing to replay, 6 had the gap covered by other legs running in between, and 2 waited out the remainder explicitly. The gap is measured from the previous leg's *finish*, which errs safe — a shared task set is always a prefix, so the colliding prompts were sent near that leg's start and are older still. Per-call latency and output tokens are therefore independent across these legs, which was not true of earlier matrices. For the underlying mechanism: A repeated request body comes back from `ete-litellm` as the *same stored response* — identical response `id`, identical `usage` — measured with the agent bypassed, on both clusters' gateways. **The TTL is ~10 minutes**, measured by survival curve (one probe per nonce at its own age: hit at 3/5/7/9 min, miss at 11/13/15/18/21). A replay re-reports the stored token counts and its latency is a cache lookup, so per-call latency and output token counts are affected; input tokens and pass rates are not (the same prompt and the same correct answer either way). This is not an agent setting — `EXGENTIC_LITELLM_CACHING=false` is pinned and provably inert against it — and **latency is not a reliable hit detector**: one measured replay took 3.0 s, the same as a miss. Compare response `id`s. Details in `docs/exgentic-agent-bug-report-20260901.md`.

## 5. Contents of the manifest file

Every run writes a `manifest.json` at its S3 prefix — a self-describing index of the run's data objects, so a client discovers the whole run in one fetch with no S3 listing. It lists the 7 data artifacts (`run.json`, `report.ndjson`, `token_report.ndjson`, `span_report.ndjson`, `report.parquet`, `token_report.parquet`, `span_report.parquet`); the manifest does **not** list itself. Each entry carries `name`, `format`, `key`, public `url`, and `size_bytes`.

Example:

```json
{
  "run_id": "20261012195705-8bd41896",
  "benchmark": "gsm8k",
  "prefix": "ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/gsm8k/20261012195705-8bd41896",
  "artifacts": [
    {
      "name": "run.json",
      "format": "json",
      "key": "ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/gsm8k/20261012195705-8bd41896/run.json",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/gsm8k/20261012195705-8bd41896/run.json",
      "size_bytes": 577
    },
    {
      "name": "report.ndjson",
      "format": "ndjson",
      "key": "ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/gsm8k/20261012195705-8bd41896/report.ndjson",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/gsm8k/20261012195705-8bd41896/report.ndjson",
      "size_bytes": 1067
    },
    {
      "name": "token_report.ndjson",
      "format": "ndjson",
      "key": "ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/gsm8k/20261012195705-8bd41896/token_report.ndjson",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/gsm8k/20261012195705-8bd41896/token_report.ndjson",
      "size_bytes": 394
    },
    {
      "name": "span_report.ndjson",
      "format": "ndjson",
      "key": "ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/gsm8k/20261012195705-8bd41896/span_report.ndjson",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/gsm8k/20261012195705-8bd41896/span_report.ndjson",
      "size_bytes": 53562
    },
    {
      "name": "report.parquet",
      "format": "parquet",
      "key": "ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/gsm8k/20261012195705-8bd41896/report.parquet",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/gsm8k/20261012195705-8bd41896/report.parquet",
      "size_bytes": 12246
    },
    {
      "name": "token_report.parquet",
      "format": "parquet",
      "key": "ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/gsm8k/20261012195705-8bd41896/token_report.parquet",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/gsm8k/20261012195705-8bd41896/token_report.parquet",
      "size_bytes": 4689
    },
    {
      "name": "span_report.parquet",
      "format": "parquet",
      "key": "ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/gsm8k/20261012195705-8bd41896/span_report.parquet",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/gsm8k/20261012195705-8bd41896/span_report.parquet",
      "size_bytes": 10680
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
| 2 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 10 | 0 | 10 | 3166 | 2077 | 5243 | 312 | 317 | 0.08 | 182 | 208 | 0.68 |
| 3 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 50 | 0 | 50 | 15684 | 8142 | 23826 | 310 | 314 | 0.06 | 150 | 163 | 0.66 |
| 4 | gsm8k | openai/Azure/gpt-4.1 | 5 | 0 | 16 | 4404 | 300 | 4704 | 809 | 881 | 0.38 | 51 | 60 | 0.36 |
| 5 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 5 | 0 | 5 | 1564 | 815 | 2379 | 306 | 313 | 0.09 | 86 | 163 | 0.76 |
| 6 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 5 | 0 | 5 | 1564 | 815 | 2379 | 306 | 313 | 0.09 | 86 | 163 | 0.94 |
| 7 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 5 | 0 | 5 | 1564 | 751 | 2315 | 306 | 313 | 0.09 | 86 | 150 | 0.66 |
| 8 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 5 | 0 | 5 | 1564 | 2031 | 3595 | 306 | 313 | 0.09 | 406 | 406 | 0.48 |
| 9 | tau2 | openai/aws/claude-sonnet-5 | 10 | 0 | 113 | 950408 | 23204 | 973612 | 93029 | 95041 | 0.11 | 2369 | 2320 | 0.20 |
| 10 | tau2 | openai/aws/claude-sonnet-5 | 20 | 0 | 219 | 1722097 | 40435 | 1762532 | 91360 | 86105 | 0.26 | 2047 | 2022 | 0.32 |
| 11 | appworld | openai/gemini-2.5-pro | 4 | 0 | 104 | 998804 | 99474 | 1098278 | 218414 | 249701 | 0.37 | 23400 | 24868 | 0.23 |
| 12 | appworld | openai/gemini-2.5-pro | 20 | 0 | 567 | 5349737 | 514407 | 5864144 | 213906 | 267487 | 0.70 | 24254 | 25720 | 0.40 |

## 7. Per-task detail

`model` is repeated on every row deliberately: token counts are only comparable within a model, and the runs do not all use the same one (#4 swaps the gsm8k model, so its ~815 input tokens per task are not comparable with the ~320 of #1–3/#5–8 on the same tasks).

`tool` is shown next to `llm` because their relationship is the tell for a damaged row: each tool call needs a preceding model turn, so `llm=1` beside `tool=11` cannot be real work — it is a lost span (§2), flagged `⚠`.

### Run #1 — gsm8k  ·  `20261012195705-8bd41896`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 86 | 406 | 1 | 1 | True |  |

### Run #2 — gsm8k  ·  `20261012202618-c8ac5eef`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 86 | 406 | 1 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 86 | 369 | 1 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 279 | 585 | 1 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 406 | 697 | 1 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 470 | 834 | 1 | 1 | True |  |
| 5 | openai/Azure/gpt-5-mini-2025-08-07 | 309 | 150 | 459 | 1 | 1 | True |  |
| 6 | openai/Azure/gpt-5-mini-2025-08-07 | 298 | 22 | 320 | 1 | 1 | True |  |
| 7 | openai/Azure/gpt-5-mini-2025-08-07 | 323 | 214 | 537 | 1 | 1 | True |  |
| 8 | openai/Azure/gpt-5-mini-2025-08-07 | 358 | 278 | 636 | 1 | 1 | True |  |
| 9 | openai/Azure/gpt-5-mini-2025-08-07 | 314 | 86 | 400 | 1 | 1 | True |  |

### Run #3 — gsm8k  ·  `20261012204439-5426e547`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 150 | 470 | 1 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 22 | 305 | 1 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 343 | 649 | 1 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 86 | 377 | 1 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 86 | 450 | 1 | 1 | True |  |
| 5 | openai/Azure/gpt-5-mini-2025-08-07 | 309 | 150 | 459 | 1 | 1 | True |  |
| 6 | openai/Azure/gpt-5-mini-2025-08-07 | 298 | 86 | 384 | 1 | 1 | True |  |
| 7 | openai/Azure/gpt-5-mini-2025-08-07 | 323 | 278 | 601 | 1 | 1 | True |  |
| 8 | openai/Azure/gpt-5-mini-2025-08-07 | 358 | 214 | 572 | 1 | 1 | True |  |
| 9 | openai/Azure/gpt-5-mini-2025-08-07 | 314 | 86 | 400 | 1 | 1 | True |  |
| 10 | openai/Azure/gpt-5-mini-2025-08-07 | 315 | 86 | 401 | 1 | 1 | True |  |
| 11 | openai/Azure/gpt-5-mini-2025-08-07 | 316 | 86 | 402 | 1 | 1 | True |  |
| 12 | openai/Azure/gpt-5-mini-2025-08-07 | 322 | 278 | 600 | 1 | 1 | True |  |
| 13 | openai/Azure/gpt-5-mini-2025-08-07 | 315 | 214 | 529 | 1 | 1 | True |  |
| 14 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 150 | 456 | 1 | 1 | True |  |
| 15 | openai/Azure/gpt-5-mini-2025-08-07 | 347 | 86 | 433 | 1 | 1 | True |  |
| 16 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 278 | 584 | 1 | 1 | True |  |
| 17 | openai/Azure/gpt-5-mini-2025-08-07 | 309 | 87 | 396 | 1 | 1 | True |  |
| 18 | openai/Azure/gpt-5-mini-2025-08-07 | 284 | 470 | 754 | 1 | 1 | True |  |
| 19 | openai/Azure/gpt-5-mini-2025-08-07 | 321 | 150 | 471 | 1 | 1 | True |  |
| 20 | openai/Azure/gpt-5-mini-2025-08-07 | 317 | 342 | 659 | 1 | 1 | True |  |
| 21 | openai/Azure/gpt-5-mini-2025-08-07 | 301 | 534 | 835 | 1 | 1 | True |  |
| 22 | openai/Azure/gpt-5-mini-2025-08-07 | 311 | 86 | 397 | 1 | 1 | True |  |
| 23 | openai/Azure/gpt-5-mini-2025-08-07 | 293 | 86 | 379 | 1 | 1 | True |  |
| 24 | openai/Azure/gpt-5-mini-2025-08-07 | 292 | 86 | 378 | 1 | 1 | True |  |
| 25 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 86 | 406 | 1 | 1 | True |  |
| 26 | openai/Azure/gpt-5-mini-2025-08-07 | 321 | 86 | 407 | 1 | 1 | True |  |
| 27 | openai/Azure/gpt-5-mini-2025-08-07 | 310 | 150 | 460 | 1 | 1 | True |  |
| 28 | openai/Azure/gpt-5-mini-2025-08-07 | 304 | 342 | 646 | 1 | 1 | True |  |
| 29 | openai/Azure/gpt-5-mini-2025-08-07 | 324 | 150 | 474 | 1 | 1 | True |  |
| 30 | openai/Azure/gpt-5-mini-2025-08-07 | 292 | 342 | 634 | 1 | 1 | True |  |
| 31 | openai/Azure/gpt-5-mini-2025-08-07 | 317 | 86 | 403 | 1 | 1 | True |  |
| 32 | openai/Azure/gpt-5-mini-2025-08-07 | 297 | 86 | 383 | 1 | 1 | True |  |
| 33 | openai/Azure/gpt-5-mini-2025-08-07 | 285 | 86 | 371 | 1 | 1 | True |  |
| 34 | openai/Azure/gpt-5-mini-2025-08-07 | 297 | 86 | 383 | 1 | 1 | True |  |
| 35 | openai/Azure/gpt-5-mini-2025-08-07 | 305 | 86 | 391 | 1 | 1 | True |  |
| 36 | openai/Azure/gpt-5-mini-2025-08-07 | 297 | 86 | 383 | 1 | 1 | True |  |
| 37 | openai/Azure/gpt-5-mini-2025-08-07 | 315 | 214 | 529 | 1 | 1 | True |  |
| 38 | openai/Azure/gpt-5-mini-2025-08-07 | 300 | 150 | 450 | 1 | 1 | True |  |
| 39 | openai/Azure/gpt-5-mini-2025-08-07 | 329 | 214 | 543 | 1 | 1 | True |  |
| 40 | openai/Azure/gpt-5-mini-2025-08-07 | 308 | 214 | 522 | 1 | 1 | True |  |
| 41 | openai/Azure/gpt-5-mini-2025-08-07 | 379 | 86 | 465 | 1 | 1 | True |  |
| 42 | openai/Azure/gpt-5-mini-2025-08-07 | 336 | 86 | 422 | 1 | 1 | True |  |
| 43 | openai/Azure/gpt-5-mini-2025-08-07 | 310 | 150 | 460 | 1 | 1 | True |  |
| 44 | openai/Azure/gpt-5-mini-2025-08-07 | 326 | 150 | 476 | 1 | 1 | True |  |
| 45 | openai/Azure/gpt-5-mini-2025-08-07 | 350 | 214 | 564 | 1 | 1 | True |  |
| 46 | openai/Azure/gpt-5-mini-2025-08-07 | 346 | 150 | 496 | 1 | 1 | True |  |
| 47 | openai/Azure/gpt-5-mini-2025-08-07 | 303 | 150 | 453 | 1 | 1 | True |  |
| 48 | openai/Azure/gpt-5-mini-2025-08-07 | 294 | 86 | 380 | 1 | 1 | True |  |
| 49 | openai/Azure/gpt-5-mini-2025-08-07 | 298 | 86 | 384 | 1 | 1 | True |  |

### Run #4 — gsm8k  ·  `20261012215030-7db4c5ca`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-4.1 | 809 | 51 | 860 | 3 | 3 | True |  |
| 1 | openai/Azure/gpt-4.1 | 691 | 45 | 736 | 3 | 3 | True |  |
| 2 | openai/Azure/gpt-4.1 | 1444 | 91 | 1535 | 5 | 5 | False |  |
| 3 | openai/Azure/gpt-4.1 | 451 | 33 | 484 | 2 | 2 | True |  |
| 4 | openai/Azure/gpt-4.1 | 1009 | 80 | 1089 | 3 | 3 | True |  |

### Run #5 — gsm8k  ·  `20261012212811-6db1a975`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 86 | 406 | 1 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 86 | 369 | 1 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 407 | 713 | 1 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 150 | 441 | 1 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 86 | 450 | 1 | 1 | True |  |

### Run #6 — gsm8k  ·  `20261012214834-196b5858`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 86 | 406 | 1 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 86 | 369 | 1 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 471 | 777 | 1 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 86 | 377 | 1 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 86 | 450 | 1 | 1 | True |  |

### Run #7 — gsm8k  ·  `20261012220600-d5b294c3`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 150 | 470 | 1 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 86 | 369 | 1 | 1 | None |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 343 | 649 | 1 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 86 | 377 | 1 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 86 | 450 | 1 | 1 | True |  |

### Run #8 — gsm8k  ·  `20261012222326-aeeeaf38`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 406 | 726 | 1 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 86 | 369 | 1 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 343 | 649 | 1 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 662 | 953 | 1 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 534 | 898 | 1 | 1 | True |  |

### Run #9 — tau2  ·  `20261012213048-2f110608`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/aws/claude-sonnet-5 | 92604 | 1994 | 94598 | 12 | 12 | True |  |
| 1 | openai/aws/claude-sonnet-5 | 92106 | 2568 | 94674 | 12 | 12 | False |  |
| 2 | openai/aws/claude-sonnet-5 | 92383 | 1453 | 93836 | 11 | 11 | False |  |
| 3 | openai/aws/claude-sonnet-5 | 75687 | 1851 | 77538 | 9 | 9 | False |  |
| 4 | openai/aws/claude-sonnet-5 | 84991 | 2363 | 87354 | 10 | 10 | True |  |
| 5 | openai/aws/claude-sonnet-5 | 106450 | 2910 | 109360 | 12 | 12 | True |  |
| 6 | openai/aws/claude-sonnet-5 | 93549 | 2550 | 96099 | 11 | 11 | True |  |
| 7 | openai/aws/claude-sonnet-5 | 112389 | 2375 | 114764 | 13 | 13 | True |  |
| 8 | openai/aws/claude-sonnet-5 | 93454 | 2125 | 95579 | 11 | 11 | True |  |
| 9 | openai/aws/claude-sonnet-5 | 106795 | 3015 | 109810 | 12 | 12 | True |  |

### Run #10 — tau2  ·  `20261012203006-51e48eac`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/aws/claude-sonnet-5 | 93306 | 1889 | 95195 | 12 | 12 | True |  |
| 1 | openai/aws/claude-sonnet-5 | 92960 | 1937 | 94897 | 12 | 12 | True |  |
| 2 | openai/aws/claude-sonnet-5 | 114975 | 2157 | 117132 | 13 | 13 | True |  |
| 3 | openai/aws/claude-sonnet-5 | 76041 | 1870 | 77911 | 9 | 9 | True |  |
| 4 | openai/aws/claude-sonnet-5 | 77828 | 2262 | 80090 | 9 | 9 | False |  |
| 5 | openai/aws/claude-sonnet-5 | 136146 | 2680 | 138826 | 15 | 15 | True |  |
| 6 | openai/aws/claude-sonnet-5 | 93647 | 2456 | 96103 | 11 | 11 | True |  |
| 7 | openai/aws/claude-sonnet-5 | 100633 | 2433 | 103066 | 12 | 12 | True |  |
| 8 | openai/aws/claude-sonnet-5 | 105340 | 2342 | 107682 | 12 | 12 | True |  |
| 9 | openai/aws/claude-sonnet-5 | 94518 | 2190 | 96708 | 11 | 11 | True |  |
| 10 | openai/aws/claude-sonnet-5 | 48239 | 1423 | 49662 | 8 | 8 | True |  |
| 11 | openai/aws/claude-sonnet-5 | 65249 | 1458 | 66707 | 10 | 10 | True |  |
| 12 | openai/aws/claude-sonnet-5 | 57096 | 1503 | 58599 | 9 | 9 | True |  |
| 13 | openai/aws/claude-sonnet-5 | 73022 | 1552 | 74574 | 11 | 11 | True |  |
| 14 | openai/aws/claude-sonnet-5 | 67427 | 1250 | 68677 | 10 | 10 | True |  |
| 15 | openai/aws/claude-sonnet-5 | 89759 | 1741 | 91500 | 11 | 11 | True |  |
| 16 | openai/aws/claude-sonnet-5 | 77728 | 2476 | 80204 | 10 | 10 | True |  |
| 17 | openai/aws/claude-sonnet-5 | 48604 | 747 | 49351 | 8 | 8 | True |  |
| 18 | openai/aws/claude-sonnet-5 | 105888 | 2193 | 108081 | 14 | 14 | False |  |
| 19 | openai/aws/claude-sonnet-5 | 103691 | 3876 | 107567 | 12 | 12 | True |  |

### Run #11 — appworld  ·  `20261012204756-7f2b30d4`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 3d9a636_1 | openai/gemini-2.5-pro | 165180 | 18744 | 183924 | 20 | 10 | False |  |
| 3d9a636_2 | openai/gemini-2.5-pro | 174221 | 22115 | 196336 | 24 | 12 | False |  |
| 3d9a636_3 | openai/gemini-2.5-pro | 396795 | 33929 | 430724 | 34 | 17 | False |  |
| fd1f8fa_1 | openai/gemini-2.5-pro | 262608 | 24686 | 287294 | 26 | 13 | False |  |

### Run #12 — appworld  ·  `20261012195911-abd3bc82`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 3d9a636_1 | openai/gemini-2.5-pro | 121988 | 15746 | 137734 | 16 | 8 | False |  |
| 3d9a636_2 | openai/gemini-2.5-pro | 161077 | 18669 | 179746 | 22 | 11 | False |  |
| 3d9a636_3 | openai/gemini-2.5-pro | 173237 | 16968 | 190205 | 22 | 11 | False |  |
| 21abae1_1 | openai/gemini-2.5-pro | 75912 | 13628 | 89540 | 12 | 6 | False |  |
| 21abae1_2 | openai/gemini-2.5-pro | 93265 | 13524 | 106789 | 14 | 7 | False |  |
| 21abae1_3 | openai/gemini-2.5-pro | 80661 | 13211 | 93872 | 12 | 6 | False |  |
| 29a7b7e_1 | openai/gemini-2.5-pro | 122170 | 21436 | 143606 | 16 | 8 | False |  |
| 29a7b7e_2 | openai/gemini-2.5-pro | 192031 | 29361 | 221392 | 24 | 12 | False |  |
| 29a7b7e_3 | openai/gemini-2.5-pro | 229981 | 30408 | 260389 | 30 | 14 | None |  |
| 325d6ec_1 | openai/gemini-2.5-pro | 357576 | 27747 | 385323 | 42 | 21 | False |  |
| 325d6ec_2 | openai/gemini-2.5-pro | 292808 | 25859 | 318667 | 38 | 19 | False |  |
| 325d6ec_3 | openai/gemini-2.5-pro | 269707 | 22325 | 292032 | 36 | 18 | False |  |
| 634f342_1 | openai/gemini-2.5-pro | 843870 | 51127 | 894997 | 58 | 29 | False |  |
| 634f342_2 | openai/gemini-2.5-pro | 197830 | 24677 | 222507 | 23 | 11 | False |  |
| 634f342_3 | openai/gemini-2.5-pro | 556935 | 38695 | 595630 | 44 | 22 | False |  |
| 8749218_1 | openai/gemini-2.5-pro | 139916 | 15265 | 155181 | 20 | 9 | None |  |
| 8749218_2 | openai/gemini-2.5-pro | 230182 | 23831 | 254013 | 22 | 11 | None |  |
| fd1f8fa_1 | openai/gemini-2.5-pro | 526409 | 45115 | 571524 | 48 | 24 | False |  |
| fd1f8fa_2 | openai/gemini-2.5-pro | 301582 | 32725 | 334307 | 28 | 14 | False |  |
| fd1f8fa_3 | openai/gemini-2.5-pro | 382600 | 34090 | 416690 | 40 | 20 | False |  |


## 8. Per-task span inventory

Which spans each task actually invoked, by name. §6 and §7 report *counts*; this is what they were counted from, read straight out of each run's `span_report.ndjson`.

Read the **chat** and **tool** columns together. There is no fixed healthy chat count — a one-shot gsm8k task legitimately shows a single `chat` span, and a multi-turn tau2 task shows many. What is not possible is **`chat` ≤ 1 alongside `tool` ≥ 2**: every tool call needs a model turn to request it, so a task cannot invoke two tools off one chat span. That shape means a usage-bearing span was dropped, and this section flags it. Reading it off *counts* rather than token values is what makes it catchable on every model, including ones whose dropped span would still have reported plausible non-zero usage.

⚠ **The `chat` and `tool` columns below are raw span totals; the ⚠ flag is computed on the `counted` subset only.** Do not apply the rule by hand to these columns. A healthy gsm8k task emits two `execute_tool` spans — `initial_observation` and `submit` — of which only `submit` is counted, so its raw pair is `1`/`2` and would trip the test while its §7 pair is the innocent `1`/`1`. Filtering to `counted` is what makes this section agree with §7.

Up to `exgentic 0.3.5.dev131` each task also issued a `max_tokens=1` capability probe, so a bare `chat == 1` used to be the damage signal and **at least 2** was healthy. The probe was replaced in `dev145` by an unbilled `GET /v1/models` check that emits no span — do not resurrect that rule, and do not compare chat counts across runs that straddle the change. **This run set is from after that change**: none of its 1100 `chat` spans carries `max_tokens=1`, so its `llm` counts are real calls one-for-one with no offset to subtract.

The **select** column splits the chat spans by *what the call was for*. The agent image defaults to `enable_tool_shortlisting = True, max_selected_tools = 30`: when the MCP advertises more tools than that, every turn opens with an **extra** LLM call that carries the whole tool inventory — each name and description — and asks the model to rank it, after which only the winners' schemas go into the call that decides the action. Both kinds land in `llm_count`, so §6 and §7 cannot separate them and the spans are the only place this is visible.

| benchmark | tasks | LLM calls / task | of which select | input tokens spent selecting | output tokens spent selecting |
|---|---:|---:|---:|---:|---:|
| gsm8k | 86 | 1.1 | 0.0 (0%) | 0% | 0% |
| tau2 | 30 | 11.1 | 0.0 (0%) | 0% | 0% |
| appworld | 24 | 27.9 | 14.0 (50%) | 68% | 77% |

`gsm8k` and `tau2` measure **exactly zero** — they expose fewer tools than the threshold, so shortlisting cannot fire on them, and that is the control for the classifier rather than a dull row: it is what shows ordinary multi-turn traffic is not being counted as selection. `appworld` pairs **1:1** (335 selection calls against 335 assistant calls), and the selection call is the dearer half — **68% of its input tokens** — because it carries every name and description while the assistant call carries only 30 schemas. Read that benchmark's token and cost figures accordingly, and note the model never sees more than 30 of its tools at once.

The count excludes `max_tokens=1` capability probes (they are not turns) and is computed per task from span *order*, never from token size: a `chat` span immediately followed by another `chat` is a selection call, one followed by a tool span is the assistant call that requested that tool. So it is the `chat` column, not the raw span total, that the `select` column is a subset of.

`not counted` are spans the aggregator cannot see: it only folds a chat/tool span into `llm_count`/`tool_count` when its parent is the `invoke_agent` span, so anything nested deeper is real work missing from the totals. A non-zero figure there is not a bug by itself — it is the known blind spot, now measurable.

The **names** column lists only the spans the Service names (`root`/`phase`) and those the agent names (`agent`/`chat`/`tool`). The `other` column counts the rest — A2A/HTTP framework internals inside the agent pod, such as `EventQueue.dequeue_event` or the ASGI `POST / http send`, which dominate the raw count (a single gsm8k task emits ~98 spans, ~90 of them framework noise) and would swamp this table. They are all present in `span_report.ndjson`; this section is the readable summary, not the full tree.

### Run #1 — gsm8k  ·  `20261012195705-8bd41896`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 102 | 1 | 0 | 2 | 94 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #2 — gsm8k  ·  `20261012202618-c8ac5eef`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 105 | 1 | 0 | 2 | 97 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 100 | 1 | 0 | 2 | 92 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 101 | 1 | 0 | 2 | 93 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 105 | 1 | 0 | 2 | 97 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 103 | 1 | 0 | 2 | 95 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 5 | 101 | 1 | 0 | 2 | 93 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 6 | 97 | 1 | 0 | 2 | 89 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 7 | 102 | 1 | 0 | 2 | 94 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 8 | 102 | 1 | 0 | 2 | 94 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 9 | 102 | 1 | 0 | 2 | 94 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #3 — gsm8k  ·  `20261012204439-5426e547`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 101 | 1 | 0 | 2 | 93 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 102 | 1 | 0 | 2 | 94 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 100 | 1 | 0 | 2 | 92 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 101 | 1 | 0 | 2 | 93 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 95 | 1 | 0 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 5 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 6 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 7 | 99 | 1 | 0 | 2 | 91 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 8 | 99 | 1 | 0 | 2 | 91 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 9 | 96 | 1 | 0 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 10 | 98 | 1 | 0 | 2 | 90 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 11 | 104 | 1 | 0 | 2 | 96 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 12 | 103 | 1 | 0 | 2 | 95 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 13 | 102 | 1 | 0 | 2 | 94 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 14 | 102 | 1 | 0 | 2 | 94 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 15 | 102 | 1 | 0 | 2 | 94 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 16 | 104 | 1 | 0 | 2 | 96 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 17 | 96 | 1 | 0 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 18 | 102 | 1 | 0 | 2 | 94 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 19 | 100 | 1 | 0 | 2 | 92 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 20 | 107 | 1 | 0 | 2 | 99 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 21 | 109 | 1 | 0 | 2 | 101 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 22 | 102 | 1 | 0 | 2 | 94 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 23 | 104 | 1 | 0 | 2 | 96 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 24 | 100 | 1 | 0 | 2 | 92 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 25 | 100 | 1 | 0 | 2 | 92 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 26 | 103 | 1 | 0 | 2 | 95 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 27 | 102 | 1 | 0 | 2 | 94 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 28 | 104 | 1 | 0 | 2 | 96 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 29 | 102 | 1 | 0 | 2 | 94 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 30 | 110 | 1 | 0 | 2 | 102 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 31 | 103 | 1 | 0 | 2 | 95 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 32 | 99 | 1 | 0 | 2 | 91 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 33 | 98 | 1 | 0 | 2 | 90 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 34 | 100 | 1 | 0 | 2 | 92 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 35 | 95 | 1 | 0 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 36 | 97 | 1 | 0 | 2 | 89 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 37 | 100 | 1 | 0 | 2 | 92 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 38 | 100 | 1 | 0 | 2 | 92 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 39 | 106 | 1 | 0 | 2 | 98 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 40 | 115 | 1 | 0 | 2 | 107 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 41 | 100 | 1 | 0 | 2 | 92 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 42 | 108 | 1 | 0 | 2 | 100 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 43 | 108 | 1 | 0 | 2 | 100 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 44 | 104 | 1 | 0 | 2 | 96 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 45 | 99 | 1 | 0 | 2 | 91 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 46 | 100 | 1 | 0 | 2 | 92 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 47 | 102 | 1 | 0 | 2 | 94 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 48 | 99 | 1 | 0 | 2 | 91 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 49 | 101 | 1 | 0 | 2 | 93 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #4 — gsm8k  ·  `20261012215030-7db4c5ca`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 150 | 3 | 0 | 4 | 138 | 1 | `chat Azure/gpt-4.1` x3, `execute_tool calculate_expression` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 127 | 3 | 0 | 4 | 115 | 1 | `chat Azure/gpt-4.1` x3, `execute_tool calculate_expression` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 167 | 5 | 0 | 6 | 151 | 1 | `chat Azure/gpt-4.1` x5, `execute_tool calculate_expression` x4, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 121 | 2 | 0 | 3 | 111 | 1 | `chat Azure/gpt-4.1` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool calculate_expression`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 143 | 3 | 0 | 4 | 131 | 1 | `chat Azure/gpt-4.1` x3, `execute_tool calculate_expression` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #5 — gsm8k  ·  `20261012212811-6db1a975`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 106 | 1 | 0 | 2 | 98 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 106 | 1 | 0 | 2 | 98 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 102 | 1 | 0 | 2 | 94 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 106 | 1 | 0 | 2 | 98 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #6 — gsm8k  ·  `20261012214834-196b5858`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 107 | 1 | 0 | 2 | 99 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 99 | 1 | 0 | 2 | 91 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 120 | 1 | 0 | 2 | 112 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 116 | 1 | 0 | 2 | 108 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 108 | 1 | 0 | 2 | 100 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #7 — gsm8k  ·  `20261012220600-d5b294c3`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 106 | 1 | 0 | 2 | 98 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 106 | 1 | 0 | 2 | 99 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit` |
| 2 | 119 | 1 | 0 | 2 | 111 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 113 | 1 | 0 | 2 | 105 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 98 | 1 | 0 | 2 | 90 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #8 — gsm8k  ·  `20261012222326-aeeeaf38`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 108 | 1 | 0 | 2 | 100 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 110 | 1 | 0 | 2 | 102 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 100 | 1 | 0 | 2 | 92 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 115 | 1 | 0 | 2 | 107 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 98 | 1 | 0 | 2 | 90 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #9 — tau2  ·  `20261012213048-2f110608`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 363 | 12 | 0 | 13 | 333 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x6, `execute_tool get_order_details` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 1 | 430 | 12 | 0 | 13 | 400 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x5, `execute_tool get_order_details` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `execute_tool transfer_to_human_agents`, `Evaluator.Evaluate` |
| 2 | 338 | 11 | 0 | 12 | 310 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 3 | 371 | 9 | 0 | 10 | 347 | 1 | `chat aws/claude-sonnet-5` x9, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 4 | 325 | 10 | 0 | 11 | 299 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool get_order_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 5 | 388 | 12 | 0 | 13 | 358 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 6 | 465 | 11 | 0 | 12 | 437 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 7 | 383 | 13 | 0 | 14 | 351 | 1 | `chat aws/claude-sonnet-5` x13, `execute_tool message` x8, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 8 | 404 | 11 | 0 | 12 | 376 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 9 | 384 | 12 | 0 | 13 | 354 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |

### Run #10 — tau2  ·  `20261012203006-51e48eac`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 543 | 12 | 0 | 13 | 513 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 1 | 445 | 12 | 0 | 13 | 415 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x6, `execute_tool get_order_details` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 2 | 571 | 13 | 0 | 14 | 539 | 1 | `chat aws/claude-sonnet-5` x13, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 3 | 428 | 9 | 0 | 10 | 404 | 1 | `chat aws/claude-sonnet-5` x9, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 4 | 414 | 9 | 0 | 10 | 390 | 1 | `chat aws/claude-sonnet-5` x9, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 5 | 637 | 15 | 0 | 16 | 601 | 1 | `chat aws/claude-sonnet-5` x15, `execute_tool message` x10, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 6 | 421 | 11 | 0 | 12 | 393 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 7 | 481 | 12 | 0 | 13 | 451 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 8 | 438 | 12 | 0 | 13 | 408 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 9 | 412 | 11 | 0 | 12 | 384 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 10 | 304 | 8 | 0 | 9 | 282 | 1 | `chat aws/claude-sonnet-5` x8, `execute_tool message` x4, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool transfer_to_human_agents`, `Evaluator.Evaluate` |
| 11 | 387 | 10 | 0 | 11 | 361 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 12 | 412 | 9 | 0 | 10 | 388 | 1 | `chat aws/claude-sonnet-5` x9, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool transfer_to_human_agents`, `Evaluator.Evaluate` |
| 13 | 414 | 11 | 0 | 12 | 386 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 14 | 370 | 10 | 0 | 11 | 344 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 15 | 440 | 11 | 0 | 12 | 412 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 16 | 382 | 10 | 0 | 11 | 356 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool cancel_pending_order`, `Evaluator.Evaluate` |
| 17 | 302 | 8 | 0 | 9 | 280 | 1 | `chat aws/claude-sonnet-5` x8, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_order_details`, `execute_tool modify_pending_order_address`, `Evaluator.Evaluate` |
| 18 | 624 | 14 | 0 | 15 | 590 | 1 | `chat aws/claude-sonnet-5` x14, `execute_tool message` x9, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 19 | 541 | 12 | 0 | 13 | 511 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool return_delivered_order_items`, `execute_tool transfer_to_human_agents`, `Evaluator.Evaluate` |

### Run #11 — appworld  ·  `20261012204756-7f2b30d4`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 3d9a636_1 | 873 | 20 | 10 | 11 | 837 | 1 | `chat gemini-2.5-pro` x20, `execute_tool phone__search_contacts` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool phone__show_contact_relationships`, `execute_tool venmo__search_friends`, `execute_tool venmo__add_friend`, `execute_tool venmo__remove_friend`, `execute_tool finish`, `Evaluator.Evaluate` |
| 3d9a636_2 | 1136 | 24 | 12 | 13 | 1094 | 1 | `chat gemini-2.5-pro` x24, `execute_tool phone__login` x2, `execute_tool phone__search_contacts` x2, `execute_tool venmo__add_friend` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool phone__show_contact_relationships`, `execute_tool venmo__search_friends`, `execute_tool venmo__remove_friend`, `execute_tool finish`, `Evaluator.Evaluate` |
| 3d9a636_3 | 1542 | 34 | 17 | 18 | 1485 | 1 | `chat gemini-2.5-pro` x34, `execute_tool venmo__search_friends` x5, `execute_tool phone__login` x2, `execute_tool phone__search_contacts` x2, `execute_tool venmo__add_friend` x2, `execute_tool venmo__remove_friend` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool supervisor__show_profile`, `execute_tool phone__show_contact_relationships`, `execute_tool finish`, `Evaluator.Evaluate` |
| fd1f8fa_1 | 1087 | 26 | 13 | 14 | 1042 | 1 | `chat gemini-2.5-pro` x26, `execute_tool spotify__play_music` x4, `execute_tool spotify__show_liked_songs` x3, `execute_tool spotify__remove_song_from_queue` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool spotify__show_song_queue`, `execute_tool finish`, `Evaluator.Evaluate` |

### Run #12 — appworld  ·  `20261012195911-abd3bc82`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 3d9a636_1 | 568 | 16 | 8 | 9 | 538 | 1 | `chat gemini-2.5-pro` x16, `execute_tool phone__search_contacts` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool phone__login`, `execute_tool venmo__add_friend`, `execute_tool venmo__remove_friend`, `execute_tool finish`, `Evaluator.Evaluate` |
| 3d9a636_2 | 676 | 22 | 11 | 12 | 637 | 1 | `chat gemini-2.5-pro` x22, `execute_tool phone__search_contacts` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool supervisor__show_profile`, `execute_tool venmo__login`, `execute_tool phone__login`, `execute_tool phone__show_contact_relationships`, `execute_tool venmo__search_friends`, `execute_tool venmo__add_friend`, `execute_tool venmo__remove_friend`, `execute_tool finish`, `Evaluator.Evaluate` |
| 3d9a636_3 | 638 | 22 | 11 | 12 | 599 | 1 | `chat gemini-2.5-pro` x22, `execute_tool venmo__search_friends` x2, `execute_tool phone__login` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool phone__show_contact_relationships`, `execute_tool phone__search_contacts`, `execute_tool venmo__add_friend`, `execute_tool venmo__remove_friend`, `execute_tool finish`, `Evaluator.Evaluate` |
| 21abae1_1 | 427 | 12 | 6 | 7 | 403 | 1 | `chat gemini-2.5-pro` x12, `execute_tool venmo__show_transactions` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool supervisor__show_profile`, `execute_tool venmo__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| 21abae1_2 | 433 | 14 | 7 | 8 | 406 | 1 | `chat gemini-2.5-pro` x14, `execute_tool venmo__show_transactions` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool phone__get_current_date_and_time`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| 21abae1_3 | 424 | 12 | 6 | 7 | 400 | 1 | `chat gemini-2.5-pro` x12, `execute_tool venmo__show_transactions` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool phone__get_current_date_and_time`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| 29a7b7e_1 | 677 | 16 | 8 | 9 | 647 | 1 | `chat gemini-2.5-pro` x16, `execute_tool file_system__create_directory` x2, `execute_tool file_system__move_file` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool file_system__login`, `execute_tool file_system__show_directory`, `execute_tool finish`, `Evaluator.Evaluate` |
| 29a7b7e_2 | 914 | 24 | 12 | 13 | 872 | 1 | `chat gemini-2.5-pro` x24, `execute_tool file_system__move_file` x5, `execute_tool file_system__show_directory` x2, `execute_tool file_system__create_directory` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool file_system__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| 29a7b7e_3 | 830 | 30 | 15 | 15 | 781 | 1 | `chat gemini-2.5-pro` x30, `execute_tool file_system__move_file` x8, `execute_tool file_system__show_directory` x2, `execute_tool file_system__create_directory` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool file_system__login` |
| 325d6ec_1 | 962 | 42 | 21 | 22 | 893 | 1 | `chat gemini-2.5-pro` x42, `execute_tool spotify__show_song_privates` x6, `execute_tool spotify__previous_song` x5, `execute_tool spotify__show_song` x5, `execute_tool spotify__show_current_song` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| 325d6ec_2 | 909 | 38 | 19 | 20 | 846 | 1 | `chat gemini-2.5-pro` x38, `execute_tool spotify__next_song` x10, `execute_tool spotify__show_downloaded_songs` x3, `execute_tool spotify__show_current_song` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool spotify__show_song_queue`, `execute_tool finish`, `Evaluator.Evaluate` |
| 325d6ec_3 | 791 | 36 | 18 | 19 | 731 | 1 | `chat gemini-2.5-pro` x36, `execute_tool spotify__show_current_song` x6, `execute_tool spotify__show_song_privates` x5, `execute_tool spotify__next_song` x4, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| 634f342_1 | 1841 | 58 | 29 | 30 | 1748 | 1 | `chat gemini-2.5-pro` x58, `execute_tool spotify__show_song` x6, `execute_tool spotify__add_song_to_playlist` x6, `execute_tool spotify__remove_song_from_playlist` x6, `execute_tool spotify__search_artists` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool supervisor__show_profile`, `execute_tool file_system__login`, `execute_tool file_system__show_file`, `execute_tool spotify__create_playlist`, `execute_tool spotify__search_songs`, `execute_tool spotify__show_genres`, `execute_tool spotify__show_playlist_library`, `execute_tool finish`, `Evaluator.Evaluate` |
| 634f342_2 | 1202 | 23 | 11 | 12 | 1162 | 1 | `chat gemini-2.5-pro` x23, `execute_tool spotify__show_song` x3, `execute_tool spotify__remove_song_from_playlist` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool file_system__login`, `execute_tool file_system__show_file`, `execute_tool spotify__create_playlist`, `execute_tool spotify__show_playlist_library`, `Evaluator.Evaluate` |
| 634f342_3 | 1522 | 44 | 22 | 23 | 1450 | 1 | `chat gemini-2.5-pro` x44, `execute_tool spotify__show_song` x7, `execute_tool spotify__remove_song_from_playlist` x7, `execute_tool spotify__show_playlist_library` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool file_system__login`, `execute_tool file_system__show_file`, `execute_tool spotify__create_playlist`, `execute_tool finish`, `Evaluator.Evaluate` |
| 8749218_1 | 549 | 20 | 10 | 10 | 515 | 1 | `chat gemini-2.5-pro` x20, `execute_tool spotify__show_recommendations` x3, `execute_tool spotify__login` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__clear_song_queue`, `execute_tool spotify__add_to_queue`, `execute_tool spotify__create_playlist` |
| 8749218_2 | 902 | 22 | 11 | 12 | 864 | 1 | `chat gemini-2.5-pro` x22, `execute_tool spotify__add_to_queue` x3, `execute_tool spotify__show_recommendations` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool spotify__clear_song_queue`, `execute_tool spotify__show_song`, `execute_tool spotify__create_playlist`, `execute_tool spotify__add_song_to_playlist` |
| fd1f8fa_1 | 1279 | 48 | 24 | 25 | 1201 | 1 | `chat gemini-2.5-pro` x48, `execute_tool spotify__remove_song_from_queue` x11, `execute_tool spotify__play_music` x6, `execute_tool spotify__show_liked_songs` x2, `execute_tool spotify__show_song_queue` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| fd1f8fa_2 | 963 | 28 | 14 | 15 | 915 | 1 | `chat gemini-2.5-pro` x28, `execute_tool spotify__play_music` x5, `execute_tool spotify__show_song_queue` x3, `execute_tool spotify__show_liked_songs` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool spotify__remove_song_from_queue`, `execute_tool finish`, `Evaluator.Evaluate` |
| fd1f8fa_3 | 1038 | 40 | 20 | 21 | 972 | 1 | `chat gemini-2.5-pro` x40, `execute_tool spotify__remove_song_from_queue` x11, `execute_tool spotify__show_liked_songs` x3, `execute_tool spotify__play_music` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool spotify__show_song_queue`, `execute_tool finish`, `Evaluator.Evaluate` |


## Reproducing this report

```sh
python3 reference/gen-12run-report.py /tmp/autobench/run12-ykt3-v136-20261012.json v1.36 "OpenShift — ykt3, single-cluster Helm install, agent runner direct (ETE-ext gateway)" docs/results/v1.36-2026-10-12/12run-ocp.md
```

The run JSON and the mirrored artifacts it points at are produced by `reference/run-12.py`; if `/tmp` has been pruned since, re-hydrate the mirror with `reference/remirror.py` first — the generators treat a missing artifact as an empty one and will quietly emit a much shorter report.
