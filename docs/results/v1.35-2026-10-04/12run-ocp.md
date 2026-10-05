# AutoBench Service — 12 Parameterized Runs (OpenShift — ykt3, single-cluster Helm install, agent runner direct (ETE-ext gateway))

**Report generated:** 2026-10-05T01:19:48Z  
**Service version:** `v1.35`  
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
| `request_max_tokens` | The `max_tokens` the agent asked for, `null` when it asked for none (the normal case). A value of `1` marks a capability probe rather than real work: agents up to `exgentic 0.3.5.dev131` issued one per task and it was counted as an LLM call, while `dev145` replaced it with an unbilled `GET /v1/models` check that emits no span. **This run set is from after that change**: none of its 1183 `chat` spans carries `max_tokens=1`, so its `llm` counts are real calls one-for-one with no offset to subtract. The column is the unambiguous way to tell a probe from a real call, and the probe's code path still exists upstream (`strict=True` in the agent's `health.py`), so it stays. |

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
| 1 | gsm8k | `20261004222426-edc7505b` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 1 | 0/1 | 0 | 4 |
| 2 | gsm8k | `20261004225140-a7c07589` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 10 | 0/10 | 0 | 29 |
| 3 | gsm8k | `20261004230851-b00aab28` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 50 | 0/50 | 0 | 34 |
| 4 | gsm8k | `20261004235422-deb12d00` | openai/Azure/gpt-4.1 | succeeded | 0.8 | 4 | 0/5 | 0 | 9 |
| 5 | gsm8k | `20261004233516-8e518b04` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 5 | 0/5 | 0 | 6 |
| 6 | gsm8k | `20261004235231-7efce0ea` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 5 | 0/5 | 0 | 6 |
| 7 | gsm8k | `20261005000952-36a452eb` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 0.8 | 4 | 1/5 | 0 | 9 |
| 8 | gsm8k | `20261005002712-d3c5e6c1` | openai/Azure/gpt-5-mini-2025-08-07 | succeeded | 1.0 | 5 | 0/5 | 0 | 7 |
| 9 | tau2 | `20261004233802-889d6a48` | openai/aws/claude-sonnet-5 | succeeded | 0.9 | 9 | 0/10 | 0 | 511 |
| 10 | tau2 | `20261004225452-a585ceca` | openai/aws/claude-sonnet-5 | succeeded | 0.9 | 18 | 0/20 | 0 | 282 |
| 11 | appworld | `20261004231137-6e7f62d8` | openai/gemini-2.5-pro | succeeded | 0.0 | 0 | 1/5 | 0 | 1284 |
| 12 | appworld | `20261004222626-0830acbe` | openai/gemini-2.5-pro | succeeded | 0.0 | 0 | 3/20 | 0 | 1402 |

**Token attribution:** complete — no row lost its usage-bearing span.

**Health probe:** every task reached the model — no task was lost to the agent's per-task `GET /v1/models` check.

**✅ These runs were spaced to defeat the gateway's completion cache.** The legs do repeat tasks — gsm8k #1/#2/#3/#5/#6/#7/#8 share 10 task ids; tau2 #9/#10 share 10 task ids; appworld on `openai/gemini-2.5-pro` #11/#12 share 5 task ids — but the driver rested every (benchmark, model) prompt set for at least **900 s** before reusing it, against a measured cache TTL of ~10 min, so **no leg could replay another's completions**: 4 leg(s) were the first of their prompt set and had nothing to replay, 4 had the gap covered by other legs running in between, and 4 waited out the remainder explicitly. The gap is measured from the previous leg's *finish*, which errs safe — a shared task set is always a prefix, so the colliding prompts were sent near that leg's start and are older still. Per-call latency and output tokens are therefore independent across these legs, which was not true of earlier matrices. For the underlying mechanism: A repeated request body comes back from `ete-litellm` as the *same stored response* — identical response `id`, identical `usage` — measured with the agent bypassed, on both clusters' gateways. **The TTL is ~10 minutes**, measured by survival curve (one probe per nonce at its own age: hit at 3/5/7/9 min, miss at 11/13/15/18/21). A replay re-reports the stored token counts and its latency is a cache lookup, so per-call latency and output token counts are affected; input tokens and pass rates are not (the same prompt and the same correct answer either way). This is not an agent setting — `EXGENTIC_LITELLM_CACHING=false` is pinned and provably inert against it — and **latency is not a reliable hit detector**: one measured replay took 3.0 s, the same as a miss. Compare response `id`s. Details in `docs/exgentic-agent-bug-report-20260901.md`.

## 5. Contents of the manifest file

Every run writes a `manifest.json` at its S3 prefix — a self-describing index of the run's data objects, so a client discovers the whole run in one fetch with no S3 listing. It lists the 7 data artifacts (`run.json`, `report.ndjson`, `token_report.ndjson`, `span_report.ndjson`, `report.parquet`, `token_report.parquet`, `span_report.parquet`); the manifest does **not** list itself. Each entry carries `name`, `format`, `key`, public `url`, and `size_bytes`.

Example:

```json
{
  "run_id": "20261004222426-edc7505b",
  "benchmark": "gsm8k",
  "prefix": "ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/gsm8k/20261004222426-edc7505b",
  "artifacts": [
    {
      "name": "run.json",
      "format": "json",
      "key": "ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/gsm8k/20261004222426-edc7505b/run.json",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/gsm8k/20261004222426-edc7505b/run.json",
      "size_bytes": 467
    },
    {
      "name": "report.ndjson",
      "format": "ndjson",
      "key": "ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/gsm8k/20261004222426-edc7505b/report.ndjson",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/gsm8k/20261004222426-edc7505b/report.ndjson",
      "size_bytes": 1012
    },
    {
      "name": "token_report.ndjson",
      "format": "ndjson",
      "key": "ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/gsm8k/20261004222426-edc7505b/token_report.ndjson",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/gsm8k/20261004222426-edc7505b/token_report.ndjson",
      "size_bytes": 370
    },
    {
      "name": "span_report.ndjson",
      "format": "ndjson",
      "key": "ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/gsm8k/20261004222426-edc7505b/span_report.ndjson",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/gsm8k/20261004222426-edc7505b/span_report.ndjson",
      "size_bytes": 50487
    },
    {
      "name": "report.parquet",
      "format": "parquet",
      "key": "ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/gsm8k/20261004222426-edc7505b/report.parquet",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/gsm8k/20261004222426-edc7505b/report.parquet",
      "size_bytes": 11984
    },
    {
      "name": "token_report.parquet",
      "format": "parquet",
      "key": "ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/gsm8k/20261004222426-edc7505b/token_report.parquet",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/gsm8k/20261004222426-edc7505b/token_report.parquet",
      "size_bytes": 4425
    },
    {
      "name": "span_report.parquet",
      "format": "parquet",
      "key": "ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/gsm8k/20261004222426-edc7505b/span_report.parquet",
      "url": "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/gsm8k/20261004222426-edc7505b/span_report.parquet",
      "size_bytes": 10406
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
| 2 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 10 | 0 | 10 | 3166 | 1949 | 5115 | 312 | 317 | 0.08 | 150 | 195 | 0.61 |
| 3 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 50 | 0 | 50 | 15684 | 8014 | 23698 | 310 | 314 | 0.06 | 86 | 160 | 0.76 |
| 4 | gsm8k | openai/Azure/gpt-4.1 | 5 | 0 | 14 | 3908 | 314 | 4222 | 807 | 782 | 0.38 | 50 | 63 | 0.45 |
| 5 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 5 | 0 | 5 | 1564 | 1071 | 2635 | 306 | 313 | 0.09 | 86 | 214 | 0.76 |
| 6 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 5 | 0 | 5 | 1564 | 623 | 2187 | 306 | 313 | 0.09 | 86 | 125 | 0.62 |
| 7 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 5 | 0 | 5 | 1564 | 943 | 2507 | 306 | 313 | 0.09 | 86 | 189 | 0.70 |
| 8 | gsm8k | openai/Azure/gpt-5-mini-2025-08-07 | 5 | 0 | 5 | 1564 | 751 | 2315 | 306 | 313 | 0.09 | 86 | 150 | 0.66 |
| 9 | tau2 | openai/aws/claude-sonnet-5 | 10 | 0 | 111 | 936612 | 22534 | 959146 | 93544 | 93661 | 0.16 | 2275 | 2253 | 0.16 |
| 10 | tau2 | openai/aws/claude-sonnet-5 | 20 | 0 | 219 | 1695959 | 39843 | 1735802 | 86490 | 84798 | 0.20 | 1832 | 1992 | 0.39 |
| 11 | appworld | openai/gemini-2.5-pro | 5 | 0 | 140 | 1420531 | 132371 | 1552902 | 214328 | 284106 | 0.57 | 22409 | 26474 | 0.40 |
| 12 | appworld | openai/gemini-2.5-pro | 20 | 0 | 618 | 5949228 | 519217 | 6468445 | 206296 | 297461 | 0.88 | 20792 | 25961 | 0.62 |

## 7. Per-task detail

`model` is repeated on every row deliberately: token counts are only comparable within a model, and the runs do not all use the same one (#4 swaps the gsm8k model, so its ~815 input tokens per task are not comparable with the ~320 of #1–3/#5–8 on the same tasks).

`tool` is shown next to `llm` because their relationship is the tell for a damaged row: each tool call needs a preceding model turn, so `llm=1` beside `tool=11` cannot be real work — it is a lost span (§2), flagged `⚠`.

### Run #1 — gsm8k  ·  `20261004222426-edc7505b`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 86 | 406 | 1 | 1 | True |  |

### Run #2 — gsm8k  ·  `20261004225140-a7c07589`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 86 | 406 | 1 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 150 | 433 | 1 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 343 | 649 | 1 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 86 | 377 | 1 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 86 | 450 | 1 | 1 | True |  |
| 5 | openai/Azure/gpt-5-mini-2025-08-07 | 309 | 150 | 459 | 1 | 1 | True |  |
| 6 | openai/Azure/gpt-5-mini-2025-08-07 | 298 | 406 | 704 | 1 | 1 | True |  |
| 7 | openai/Azure/gpt-5-mini-2025-08-07 | 323 | 342 | 665 | 1 | 1 | True |  |
| 8 | openai/Azure/gpt-5-mini-2025-08-07 | 358 | 214 | 572 | 1 | 1 | True |  |
| 9 | openai/Azure/gpt-5-mini-2025-08-07 | 314 | 86 | 400 | 1 | 1 | True |  |

### Run #3 — gsm8k  ·  `20261004230851-b00aab28`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 598 | 918 | 1 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 22 | 305 | 1 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 343 | 649 | 1 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 86 | 377 | 1 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 86 | 450 | 1 | 1 | True |  |
| 5 | openai/Azure/gpt-5-mini-2025-08-07 | 309 | 214 | 523 | 1 | 1 | True |  |
| 6 | openai/Azure/gpt-5-mini-2025-08-07 | 298 | 150 | 448 | 1 | 1 | True |  |
| 7 | openai/Azure/gpt-5-mini-2025-08-07 | 323 | 214 | 537 | 1 | 1 | True |  |
| 8 | openai/Azure/gpt-5-mini-2025-08-07 | 358 | 534 | 892 | 1 | 1 | True |  |
| 9 | openai/Azure/gpt-5-mini-2025-08-07 | 314 | 86 | 400 | 1 | 1 | True |  |
| 10 | openai/Azure/gpt-5-mini-2025-08-07 | 315 | 86 | 401 | 1 | 1 | True |  |
| 11 | openai/Azure/gpt-5-mini-2025-08-07 | 316 | 150 | 466 | 1 | 1 | True |  |
| 12 | openai/Azure/gpt-5-mini-2025-08-07 | 322 | 342 | 664 | 1 | 1 | True |  |
| 13 | openai/Azure/gpt-5-mini-2025-08-07 | 315 | 214 | 529 | 1 | 1 | True |  |
| 14 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 86 | 392 | 1 | 1 | True |  |
| 15 | openai/Azure/gpt-5-mini-2025-08-07 | 347 | 150 | 497 | 1 | 1 | True |  |
| 16 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 150 | 456 | 1 | 1 | True |  |
| 17 | openai/Azure/gpt-5-mini-2025-08-07 | 309 | 471 | 780 | 1 | 1 | True |  |
| 18 | openai/Azure/gpt-5-mini-2025-08-07 | 284 | 86 | 370 | 1 | 1 | True |  |
| 19 | openai/Azure/gpt-5-mini-2025-08-07 | 321 | 150 | 471 | 1 | 1 | True |  |
| 20 | openai/Azure/gpt-5-mini-2025-08-07 | 317 | 278 | 595 | 1 | 1 | True |  |
| 21 | openai/Azure/gpt-5-mini-2025-08-07 | 301 | 86 | 387 | 1 | 1 | True |  |
| 22 | openai/Azure/gpt-5-mini-2025-08-07 | 311 | 86 | 397 | 1 | 1 | True |  |
| 23 | openai/Azure/gpt-5-mini-2025-08-07 | 293 | 22 | 315 | 1 | 1 | True |  |
| 24 | openai/Azure/gpt-5-mini-2025-08-07 | 292 | 86 | 378 | 1 | 1 | True |  |
| 25 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 86 | 406 | 1 | 1 | True |  |
| 26 | openai/Azure/gpt-5-mini-2025-08-07 | 321 | 86 | 407 | 1 | 1 | True |  |
| 27 | openai/Azure/gpt-5-mini-2025-08-07 | 310 | 86 | 396 | 1 | 1 | True |  |
| 28 | openai/Azure/gpt-5-mini-2025-08-07 | 304 | 86 | 390 | 1 | 1 | True |  |
| 29 | openai/Azure/gpt-5-mini-2025-08-07 | 324 | 86 | 410 | 1 | 1 | True |  |
| 30 | openai/Azure/gpt-5-mini-2025-08-07 | 292 | 86 | 378 | 1 | 1 | True |  |
| 31 | openai/Azure/gpt-5-mini-2025-08-07 | 317 | 150 | 467 | 1 | 1 | True |  |
| 32 | openai/Azure/gpt-5-mini-2025-08-07 | 297 | 86 | 383 | 1 | 1 | True |  |
| 33 | openai/Azure/gpt-5-mini-2025-08-07 | 285 | 86 | 371 | 1 | 1 | True |  |
| 34 | openai/Azure/gpt-5-mini-2025-08-07 | 297 | 86 | 383 | 1 | 1 | True |  |
| 35 | openai/Azure/gpt-5-mini-2025-08-07 | 305 | 86 | 391 | 1 | 1 | True |  |
| 36 | openai/Azure/gpt-5-mini-2025-08-07 | 297 | 86 | 383 | 1 | 1 | True |  |
| 37 | openai/Azure/gpt-5-mini-2025-08-07 | 315 | 214 | 529 | 1 | 1 | True |  |
| 38 | openai/Azure/gpt-5-mini-2025-08-07 | 300 | 214 | 514 | 1 | 1 | True |  |
| 39 | openai/Azure/gpt-5-mini-2025-08-07 | 329 | 278 | 607 | 1 | 1 | True |  |
| 40 | openai/Azure/gpt-5-mini-2025-08-07 | 308 | 214 | 522 | 1 | 1 | True |  |
| 41 | openai/Azure/gpt-5-mini-2025-08-07 | 379 | 86 | 465 | 1 | 1 | True |  |
| 42 | openai/Azure/gpt-5-mini-2025-08-07 | 336 | 22 | 358 | 1 | 1 | True |  |
| 43 | openai/Azure/gpt-5-mini-2025-08-07 | 310 | 150 | 460 | 1 | 1 | True |  |
| 44 | openai/Azure/gpt-5-mini-2025-08-07 | 326 | 150 | 476 | 1 | 1 | True |  |
| 45 | openai/Azure/gpt-5-mini-2025-08-07 | 350 | 278 | 628 | 1 | 1 | True |  |
| 46 | openai/Azure/gpt-5-mini-2025-08-07 | 346 | 150 | 496 | 1 | 1 | True |  |
| 47 | openai/Azure/gpt-5-mini-2025-08-07 | 303 | 214 | 517 | 1 | 1 | True |  |
| 48 | openai/Azure/gpt-5-mini-2025-08-07 | 294 | 86 | 380 | 1 | 1 | True |  |
| 49 | openai/Azure/gpt-5-mini-2025-08-07 | 298 | 86 | 384 | 1 | 1 | True |  |

### Run #4 — gsm8k  ·  `20261004235422-deb12d00`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-4.1 | 807 | 50 | 857 | 3 | 3 | True |  |
| 1 | openai/Azure/gpt-4.1 | 474 | 65 | 539 | 2 | 2 | True |  |
| 2 | openai/Azure/gpt-4.1 | 1240 | 116 | 1356 | 4 | 4 | False |  |
| 3 | openai/Azure/gpt-4.1 | 451 | 33 | 484 | 2 | 2 | True |  |
| 4 | openai/Azure/gpt-4.1 | 936 | 50 | 986 | 3 | 3 | True |  |

### Run #5 — gsm8k  ·  `20261004233516-8e518b04`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 86 | 406 | 1 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 86 | 369 | 1 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 343 | 649 | 1 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 470 | 761 | 1 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 86 | 450 | 1 | 1 | True |  |

### Run #6 — gsm8k  ·  `20261004235231-7efce0ea`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 86 | 406 | 1 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 86 | 369 | 1 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 279 | 585 | 1 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 86 | 377 | 1 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 86 | 450 | 1 | 1 | True |  |

### Run #7 — gsm8k  ·  `20261005000952-36a452eb`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 86 | 406 | 1 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 86 | 369 | 1 | 1 | None |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 279 | 585 | 1 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 86 | 377 | 1 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 406 | 770 | 1 | 1 | True |  |

### Run #8 — gsm8k  ·  `20261005002712-d3c5e6c1`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/Azure/gpt-5-mini-2025-08-07 | 320 | 150 | 470 | 1 | 1 | True |  |
| 1 | openai/Azure/gpt-5-mini-2025-08-07 | 283 | 86 | 369 | 1 | 1 | True |  |
| 2 | openai/Azure/gpt-5-mini-2025-08-07 | 306 | 343 | 649 | 1 | 1 | True |  |
| 3 | openai/Azure/gpt-5-mini-2025-08-07 | 291 | 86 | 377 | 1 | 1 | True |  |
| 4 | openai/Azure/gpt-5-mini-2025-08-07 | 364 | 86 | 450 | 1 | 1 | True |  |

### Run #9 — tau2  ·  `20261004233802-889d6a48`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/aws/claude-sonnet-5 | 92931 | 1812 | 94743 | 12 | 12 | True |  |
| 1 | openai/aws/claude-sonnet-5 | 93520 | 2009 | 95529 | 12 | 12 | True |  |
| 2 | openai/aws/claude-sonnet-5 | 76562 | 1600 | 78162 | 9 | 9 | True |  |
| 3 | openai/aws/claude-sonnet-5 | 76053 | 2211 | 78264 | 9 | 9 | True |  |
| 4 | openai/aws/claude-sonnet-5 | 100597 | 2580 | 103177 | 12 | 12 | True |  |
| 5 | openai/aws/claude-sonnet-5 | 133049 | 2622 | 135671 | 14 | 14 | False |  |
| 6 | openai/aws/claude-sonnet-5 | 94116 | 2339 | 96455 | 11 | 11 | True |  |
| 7 | openai/aws/claude-sonnet-5 | 82420 | 2123 | 84543 | 10 | 10 | True |  |
| 8 | openai/aws/claude-sonnet-5 | 93567 | 2567 | 96134 | 11 | 11 | True |  |
| 9 | openai/aws/claude-sonnet-5 | 93797 | 2671 | 96468 | 11 | 11 | True |  |

### Run #10 — tau2  ·  `20261004225452-a585ceca`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 0 | openai/aws/claude-sonnet-5 | 81918 | 1669 | 83587 | 11 | 11 | True |  |
| 1 | openai/aws/claude-sonnet-5 | 91948 | 1490 | 93438 | 12 | 12 | True |  |
| 2 | openai/aws/claude-sonnet-5 | 119783 | 1791 | 121574 | 14 | 14 | True |  |
| 3 | openai/aws/claude-sonnet-5 | 101487 | 2341 | 103828 | 12 | 12 | True |  |
| 4 | openai/aws/claude-sonnet-5 | 77536 | 2770 | 80306 | 9 | 9 | True |  |
| 5 | openai/aws/claude-sonnet-5 | 105564 | 2294 | 107858 | 12 | 12 | True |  |
| 6 | openai/aws/claude-sonnet-5 | 93503 | 1931 | 95434 | 11 | 11 | True |  |
| 7 | openai/aws/claude-sonnet-5 | 105766 | 2363 | 108129 | 12 | 12 | True |  |
| 8 | openai/aws/claude-sonnet-5 | 92873 | 2515 | 95388 | 11 | 11 | True |  |
| 9 | openai/aws/claude-sonnet-5 | 93679 | 2603 | 96282 | 11 | 11 | True |  |
| 10 | openai/aws/claude-sonnet-5 | 61833 | 1545 | 63378 | 10 | 10 | True |  |
| 11 | openai/aws/claude-sonnet-5 | 66540 | 1466 | 68006 | 10 | 10 | True |  |
| 12 | openai/aws/claude-sonnet-5 | 72476 | 1722 | 74198 | 11 | 11 | True |  |
| 13 | openai/aws/claude-sonnet-5 | 72879 | 1414 | 74293 | 11 | 11 | True |  |
| 14 | openai/aws/claude-sonnet-5 | 67444 | 1373 | 68817 | 10 | 10 | True |  |
| 15 | openai/aws/claude-sonnet-5 | 89545 | 1872 | 91417 | 11 | 11 | True |  |
| 16 | openai/aws/claude-sonnet-5 | 77183 | 2015 | 79198 | 10 | 10 | True |  |
| 17 | openai/aws/claude-sonnet-5 | 48203 | 645 | 48848 | 8 | 8 | True |  |
| 18 | openai/aws/claude-sonnet-5 | 83434 | 1448 | 84882 | 12 | 12 | False |  |
| 19 | openai/aws/claude-sonnet-5 | 92365 | 4576 | 96941 | 11 | 11 | False |  |

### Run #11 — appworld  ·  `20261004231137-6e7f62d8`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 3d9a636_1 | openai/gemini-2.5-pro | 156916 | 22409 | 179325 | 20 | 10 | False |  |
| 3d9a636_2 | openai/gemini-2.5-pro | 122844 | 16124 | 138968 | 16 | 8 | False |  |
| 3d9a636_3 | openai/gemini-2.5-pro | 214328 | 21600 | 235928 | 20 | 10 | False |  |
| fd1f8fa_1 | openai/gemini-2.5-pro | 566845 | 46613 | 613458 | 52 | 26 | False |  |
| fd1f8fa_2 | openai/gemini-2.5-pro | 359598 | 25625 | 385223 | 32 | 15 | None |  |

### Run #12 — appworld  ·  `20261004222626-0830acbe`

| task | model | in | out | total | llm | tool | passed | |
|---|---|---:|---:|---:|---:|---:|---|---|
| 3d9a636_1 | openai/gemini-2.5-pro | 313449 | 25901 | 339350 | 36 | 18 | False |  |
| 3d9a636_2 | openai/gemini-2.5-pro | 85642 | 12290 | 97932 | 12 | 6 | False |  |
| 3d9a636_3 | openai/gemini-2.5-pro | 198664 | 17006 | 215670 | 24 | 12 | False |  |
| 21abae1_1 | openai/gemini-2.5-pro | 49698 | 9102 | 58800 | 8 | 4 | False |  |
| 21abae1_2 | openai/gemini-2.5-pro | 63125 | 9741 | 72866 | 10 | 5 | False |  |
| 21abae1_3 | openai/gemini-2.5-pro | 76281 | 9300 | 85581 | 12 | 6 | False |  |
| 29a7b7e_1 | openai/gemini-2.5-pro | 213928 | 28516 | 242444 | 26 | 13 | False |  |
| 29a7b7e_2 | openai/gemini-2.5-pro | 501899 | 50612 | 552511 | 54 | 27 | False |  |
| 29a7b7e_3 | openai/gemini-2.5-pro | 317044 | 33125 | 350169 | 30 | 15 | False |  |
| 325d6ec_1 | openai/gemini-2.5-pro | 247538 | 22320 | 269858 | 34 | 17 | False |  |
| 325d6ec_2 | openai/gemini-2.5-pro | 180048 | 13209 | 193257 | 26 | 13 | False |  |
| 325d6ec_3 | openai/gemini-2.5-pro | 121601 | 15238 | 136839 | 18 | 9 | False |  |
| 634f342_1 | openai/gemini-2.5-pro | 278591 | 25091 | 303682 | 28 | 14 | False |  |
| 634f342_2 | openai/gemini-2.5-pro | 1063878 | 59303 | 1123181 | 94 | 47 | False |  |
| 634f342_3 | openai/gemini-2.5-pro | 616268 | 42454 | 658722 | 60 | 30 | False |  |
| 8749218_1 | openai/gemini-2.5-pro | 791089 | 59721 | 850810 | 58 | 29 | False |  |
| 8749218_2 | openai/gemini-2.5-pro | 164089 | 19264 | 183353 | 20 | 9 | None |  |
| fd1f8fa_1 | openai/gemini-2.5-pro | 166503 | 18671 | 185174 | 18 | 8 | None |  |
| fd1f8fa_2 | openai/gemini-2.5-pro | 64206 | 9487 | 73693 | 10 | 4 | None |  |
| fd1f8fa_3 | openai/gemini-2.5-pro | 435687 | 38866 | 474553 | 40 | 20 | False |  |


## 8. Per-task span inventory

Which spans each task actually invoked, by name. §6 and §7 report *counts*; this is what they were counted from, read straight out of each run's `span_report.ndjson`.

Read the **chat** and **tool** columns together. There is no fixed healthy chat count — a one-shot gsm8k task legitimately shows a single `chat` span, and a multi-turn tau2 task shows many. What is not possible is **`chat` ≤ 1 alongside `tool` ≥ 2**: every tool call needs a model turn to request it, so a task cannot invoke two tools off one chat span. That shape means a usage-bearing span was dropped, and this section flags it. Reading it off *counts* rather than token values is what makes it catchable on every model, including ones whose dropped span would still have reported plausible non-zero usage.

⚠ **The `chat` and `tool` columns below are raw span totals; the ⚠ flag is computed on the `counted` subset only.** Do not apply the rule by hand to these columns. A healthy gsm8k task emits two `execute_tool` spans — `initial_observation` and `submit` — of which only `submit` is counted, so its raw pair is `1`/`2` and would trip the test while its §7 pair is the innocent `1`/`1`. Filtering to `counted` is what makes this section agree with §7.

Up to `exgentic 0.3.5.dev131` each task also issued a `max_tokens=1` capability probe, so a bare `chat == 1` used to be the damage signal and **at least 2** was healthy. The probe was replaced in `dev145` by an unbilled `GET /v1/models` check that emits no span — do not resurrect that rule, and do not compare chat counts across runs that straddle the change. **This run set is from after that change**: none of its 1183 `chat` spans carries `max_tokens=1`, so its `llm` counts are real calls one-for-one with no offset to subtract.

The **select** column splits the chat spans by *what the call was for*. The agent image defaults to `enable_tool_shortlisting = True, max_selected_tools = 30`: when the MCP advertises more tools than that, every turn opens with an **extra** LLM call that carries the whole tool inventory — each name and description — and asks the model to rank it, after which only the winners' schemas go into the call that decides the action. Both kinds land in `llm_count`, so §6 and §7 cannot separate them and the spans are the only place this is visible.

| benchmark | tasks | LLM calls / task | of which select | input tokens spent selecting | output tokens spent selecting |
|---|---:|---:|---:|---:|---:|
| gsm8k | 86 | 1.1 | 0.0 (0%) | 0% | 0% |
| tau2 | 30 | 11.0 | 0.0 (0%) | 0% | 0% |
| appworld | 25 | 30.3 | 15.2 (50%) | 67% | 79% |

`gsm8k` and `tau2` measure **exactly zero** — they expose fewer tools than the threshold, so shortlisting cannot fire on them, and that is the control for the classifier rather than a dull row: it is what shows ordinary multi-turn traffic is not being counted as selection. `appworld` pairs **1:1** (379 selection calls against 379 assistant calls), and the selection call is the dearer half — **67% of its input tokens** — because it carries every name and description while the assistant call carries only 30 schemas. Read that benchmark's token and cost figures accordingly, and note the model never sees more than 30 of its tools at once.

The count excludes `max_tokens=1` capability probes (they are not turns) and is computed per task from span *order*, never from token size: a `chat` span immediately followed by another `chat` is a selection call, one followed by a tool span is the assistant call that requested that tool. So it is the `chat` column, not the raw span total, that the `select` column is a subset of.

`not counted` are spans the aggregator cannot see: it only folds a chat/tool span into `llm_count`/`tool_count` when its parent is the `invoke_agent` span, so anything nested deeper is real work missing from the totals. A non-zero figure there is not a bug by itself — it is the known blind spot, now measurable.

The **names** column lists only the spans the Service names (`root`/`phase`) and those the agent names (`agent`/`chat`/`tool`). The `other` column counts the rest — A2A/HTTP framework internals inside the agent pod, such as `EventQueue.dequeue_event` or the ASGI `POST / http send`, which dominate the raw count (a single gsm8k task emits ~98 spans, ~90 of them framework noise) and would swamp this table. They are all present in `span_report.ndjson`; this section is the readable summary, not the full tree.

### Run #1 — gsm8k  ·  `20261004222426-edc7505b`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 96 | 1 | 0 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #2 — gsm8k  ·  `20261004225140-a7c07589`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 96 | 1 | 0 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 98 | 1 | 0 | 2 | 90 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 5 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 6 | 97 | 1 | 0 | 2 | 89 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 7 | 97 | 1 | 0 | 2 | 89 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 8 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 9 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #3 — gsm8k  ·  `20261004230851-b00aab28`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 100 | 1 | 0 | 2 | 92 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 117 | 1 | 0 | 2 | 109 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 5 | 95 | 1 | 0 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 6 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 7 | 95 | 1 | 0 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 8 | 99 | 1 | 0 | 2 | 91 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 9 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 10 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 11 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 12 | 97 | 1 | 0 | 2 | 89 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 13 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 14 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 15 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 16 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 17 | 97 | 1 | 0 | 2 | 89 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 18 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 19 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 20 | 95 | 1 | 0 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 21 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 22 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 23 | 92 | 1 | 0 | 2 | 84 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 24 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 25 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 26 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 27 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 28 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 29 | 92 | 1 | 0 | 2 | 84 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 30 | 92 | 1 | 0 | 2 | 84 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 31 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 32 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 33 | 92 | 1 | 0 | 2 | 84 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 34 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 35 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 36 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 37 | 96 | 1 | 0 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 38 | 95 | 1 | 0 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 39 | 96 | 1 | 0 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 40 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 41 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 42 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 43 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 44 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 45 | 95 | 1 | 0 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 46 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 47 | 95 | 1 | 0 | 2 | 87 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 48 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 49 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #4 — gsm8k  ·  `20261004235422-deb12d00`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 119 | 3 | 0 | 4 | 107 | 1 | `chat Azure/gpt-4.1` x3, `execute_tool calculate_expression` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 113 | 2 | 0 | 3 | 103 | 1 | `chat Azure/gpt-4.1` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool calculate_expression`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 139 | 4 | 0 | 5 | 125 | 1 | `chat Azure/gpt-4.1` x4, `execute_tool calculate_expression` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 108 | 2 | 0 | 3 | 98 | 1 | `chat Azure/gpt-4.1` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool calculate_expression`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 121 | 3 | 0 | 4 | 109 | 1 | `chat Azure/gpt-4.1` x3, `execute_tool calculate_expression` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #5 — gsm8k  ·  `20261004233516-8e518b04`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 96 | 1 | 0 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 94 | 1 | 0 | 2 | 86 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 98 | 1 | 0 | 2 | 90 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 100 | 1 | 0 | 2 | 92 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 93 | 1 | 0 | 2 | 85 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #6 — gsm8k  ·  `20261004235231-7efce0ea`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 97 | 1 | 0 | 2 | 89 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 100 | 1 | 0 | 2 | 92 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 100 | 1 | 0 | 2 | 92 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 97 | 1 | 0 | 2 | 89 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 92 | 1 | 0 | 2 | 84 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #7 — gsm8k  ·  `20261005000952-36a452eb`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 96 | 1 | 0 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 105 | 1 | 0 | 2 | 98 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit` |
| 2 | 98 | 1 | 0 | 2 | 90 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 96 | 1 | 0 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 98 | 1 | 0 | 2 | 90 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #8 — gsm8k  ·  `20261005002712-d3c5e6c1`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 102 | 1 | 0 | 2 | 94 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 1 | 98 | 1 | 0 | 2 | 90 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 2 | 99 | 1 | 0 | 2 | 91 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 3 | 96 | 1 | 0 | 2 | 88 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |
| 4 | 92 | 1 | 0 | 2 | 84 | 1 | `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `chat Azure/gpt-5-mini-2025-08-07`, `execute_tool submit`, `Evaluator.Evaluate` |

### Run #9 — tau2  ·  `20261004233802-889d6a48`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 320 | 12 | 0 | 13 | 290 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x6, `execute_tool get_order_details` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 1 | 299 | 12 | 0 | 13 | 269 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x6, `execute_tool get_order_details` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 2 | 271 | 9 | 0 | 10 | 247 | 1 | `chat aws/claude-sonnet-5` x9, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 3 | 285 | 9 | 0 | 10 | 261 | 1 | `chat aws/claude-sonnet-5` x9, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 4 | 327 | 12 | 0 | 13 | 297 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 5 | 365 | 14 | 0 | 15 | 331 | 1 | `chat aws/claude-sonnet-5` x14, `execute_tool message` x8, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 6 | 303 | 11 | 0 | 12 | 275 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 7 | 285 | 10 | 0 | 11 | 259 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 8 | 312 | 11 | 0 | 12 | 284 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 9 | 313 | 11 | 0 | 12 | 285 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |

### Run #10 — tau2  ·  `20261004225452-a585ceca`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 309 | 11 | 0 | 12 | 281 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x5, `execute_tool get_order_details` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 1 | 289 | 12 | 0 | 13 | 259 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x6, `execute_tool get_order_details` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 2 | 345 | 14 | 0 | 15 | 311 | 1 | `chat aws/claude-sonnet-5` x14, `execute_tool message` x8, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 3 | 323 | 12 | 0 | 13 | 293 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 4 | 289 | 9 | 0 | 10 | 265 | 1 | `chat aws/claude-sonnet-5` x9, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool list_all_product_types`, `execute_tool get_product_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 5 | 328 | 12 | 0 | 13 | 298 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 6 | 292 | 11 | 0 | 12 | 264 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 7 | 313 | 12 | 0 | 13 | 283 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 8 | 307 | 11 | 0 | 12 | 279 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 9 | 303 | 11 | 0 | 12 | 275 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool exchange_delivered_order_items`, `Evaluator.Evaluate` |
| 10 | 254 | 10 | 0 | 11 | 228 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool transfer_to_human_agents`, `Evaluator.Evaluate` |
| 11 | 268 | 10 | 0 | 11 | 242 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 12 | 272 | 11 | 0 | 12 | 244 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `execute_tool transfer_to_human_agents`, `Evaluator.Evaluate` |
| 13 | 269 | 11 | 0 | 12 | 241 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x7, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 14 | 270 | 10 | 0 | 11 | 244 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_email`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 15 | 278 | 11 | 0 | 12 | 250 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool modify_pending_order_items`, `Evaluator.Evaluate` |
| 16 | 275 | 10 | 0 | 11 | 249 | 1 | `chat aws/claude-sonnet-5` x10, `execute_tool message` x6, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool cancel_pending_order`, `Evaluator.Evaluate` |
| 17 | 200 | 8 | 0 | 9 | 178 | 1 | `chat aws/claude-sonnet-5` x8, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_order_details`, `execute_tool modify_pending_order_address`, `Evaluator.Evaluate` |
| 18 | 286 | 12 | 0 | 13 | 256 | 1 | `chat aws/claude-sonnet-5` x12, `execute_tool message` x8, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool return_delivered_order_items`, `Evaluator.Evaluate` |
| 19 | 342 | 11 | 0 | 12 | 314 | 1 | `chat aws/claude-sonnet-5` x11, `execute_tool message` x5, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool find_user_id_by_name_zip`, `execute_tool get_user_details`, `execute_tool get_order_details`, `execute_tool get_product_details`, `execute_tool return_delivered_order_items`, `execute_tool transfer_to_human_agents`, `Evaluator.Evaluate` |

### Run #11 — appworld  ·  `20261004231137-6e7f62d8`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 3d9a636_1 | 684 | 20 | 10 | 11 | 648 | 1 | `chat gemini-2.5-pro` x20, `execute_tool phone__search_contacts` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool supervisor__show_profile`, `execute_tool venmo__login`, `execute_tool phone__login`, `execute_tool venmo__add_friend`, `execute_tool venmo__remove_friend`, `execute_tool finish`, `Evaluator.Evaluate` |
| 3d9a636_2 | 544 | 16 | 8 | 9 | 514 | 1 | `chat gemini-2.5-pro` x16, `execute_tool phone__login` x2, `execute_tool phone__search_contacts` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__add_friend`, `execute_tool venmo__remove_friend`, `execute_tool finish`, `Evaluator.Evaluate` |
| 3d9a636_3 | 780 | 20 | 10 | 11 | 744 | 1 | `chat gemini-2.5-pro` x20, `execute_tool phone__search_contacts` x2, `execute_tool venmo__search_friends` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool phone__login`, `execute_tool phone__show_contact_relationships`, `execute_tool venmo__add_friend`, `execute_tool finish`, `Evaluator.Evaluate` |
| fd1f8fa_1 | 1275 | 52 | 26 | 27 | 1191 | 1 | `chat gemini-2.5-pro` x52, `execute_tool spotify__remove_song_from_queue` x11, `execute_tool spotify__play_music` x4, `execute_tool spotify__show_liked_songs` x3, `execute_tool spotify__show_song_queue` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool spotify__next_song`, `execute_tool spotify__show_current_song`, `execute_tool spotify__previous_song`, `execute_tool finish`, `Evaluator.Evaluate` |
| fd1f8fa_2 | 796 | 32 | 16 | 16 | 744 | 1 | `chat gemini-2.5-pro` x32, `execute_tool spotify__show_song_queue` x5, `execute_tool spotify__show_song_privates` x4, `execute_tool spotify__remove_song_from_queue` x4, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login` |

### Run #12 — appworld  ·  `20261004222626-0830acbe`

| task | spans | chat | select | tool | other | not counted | span names (xN) |
|---|---:|---:|---:|---:|---:|---:|---|
| 3d9a636_1 | 800 | 36 | 18 | 19 | 740 | 1 | `chat gemini-2.5-pro` x36, `execute_tool venmo__remove_friend` x6, `execute_tool venmo__add_friend` x4, `execute_tool phone__search_contacts` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool phone__login`, `execute_tool venmo__login`, `execute_tool phone__show_contact_relationships`, `execute_tool venmo__search_friends`, `execute_tool finish`, `Evaluator.Evaluate` |
| 3d9a636_2 | 452 | 12 | 6 | 7 | 428 | 1 | `chat gemini-2.5-pro` x12, `execute_tool phone__search_contacts` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool phone__login`, `execute_tool venmo__add_friend`, `execute_tool finish`, `Evaluator.Evaluate` |
| 3d9a636_3 | 611 | 24 | 12 | 13 | 569 | 1 | `chat gemini-2.5-pro` x24, `execute_tool phone__search_contacts` x3, `execute_tool venmo__search_friends` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool phone__show_contact_relationships`, `execute_tool venmo__add_friend`, `execute_tool venmo__remove_friend`, `execute_tool finish`, `Evaluator.Evaluate` |
| 21abae1_1 | 298 | 8 | 4 | 5 | 280 | 1 | `chat gemini-2.5-pro` x8, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool venmo__show_transactions`, `execute_tool finish`, `Evaluator.Evaluate` |
| 21abae1_2 | 328 | 10 | 5 | 6 | 307 | 1 | `chat gemini-2.5-pro` x10, `execute_tool venmo__show_transactions` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| 21abae1_3 | 323 | 12 | 6 | 7 | 299 | 1 | `chat gemini-2.5-pro` x12, `execute_tool venmo__show_transactions` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool venmo__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| 29a7b7e_1 | 846 | 26 | 13 | 14 | 801 | 1 | `chat gemini-2.5-pro` x26, `execute_tool file_system__move_file` x5, `execute_tool file_system__create_directory` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_profile`, `execute_tool supervisor__show_account_passwords`, `execute_tool file_system__login`, `execute_tool file_system__show_directory`, `execute_tool file_system__delete_directory`, `execute_tool finish`, `Evaluator.Evaluate` |
| 29a7b7e_2 | 1271 | 54 | 27 | 28 | 1184 | 1 | `chat gemini-2.5-pro` x54, `execute_tool file_system__move_file` x20, `execute_tool file_system__create_directory` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool supervisor__show_profile`, `execute_tool file_system__login`, `execute_tool file_system__show_directory`, `execute_tool finish`, `Evaluator.Evaluate` |
| 29a7b7e_3 | 967 | 30 | 15 | 16 | 916 | 1 | `chat gemini-2.5-pro` x30, `execute_tool file_system__show_directory` x4, `execute_tool file_system__move_file` x3, `execute_tool file_system__login` x2, `execute_tool file_system__create_directory` x2, `execute_tool file_system__delete_directory` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool finish`, `Evaluator.Evaluate` |
| 325d6ec_1 | 715 | 34 | 17 | 18 | 658 | 1 | `chat gemini-2.5-pro` x34, `execute_tool spotify__show_song_privates` x6, `execute_tool spotify__previous_song` x5, `execute_tool spotify__show_current_song` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool supervisor__show_profile`, `execute_tool spotify__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| 325d6ec_2 | 497 | 26 | 13 | 14 | 452 | 1 | `chat gemini-2.5-pro` x26, `execute_tool spotify__show_current_song` x4, `execute_tool spotify__next_song` x4, `execute_tool spotify__show_downloaded_songs` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool finish`, `Evaluator.Evaluate` |
| 325d6ec_3 | 469 | 18 | 9 | 10 | 436 | 1 | `chat gemini-2.5-pro` x18, `execute_tool spotify__next_song` x4, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool spotify__show_liked_songs`, `execute_tool spotify__show_current_song`, `execute_tool finish`, `Evaluator.Evaluate` |
| 634f342_1 | 821 | 28 | 14 | 15 | 773 | 1 | `chat gemini-2.5-pro` x28, `execute_tool file_system__show_file` x2, `execute_tool file_system__show_directory` x2, `execute_tool spotify__show_playlist_library` x2, `execute_tool spotify__show_song` x2, `execute_tool spotify__remove_song_from_playlist` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool file_system__login`, `execute_tool spotify__create_playlist`, `execute_tool finish`, `Evaluator.Evaluate` |
| 634f342_2 | 1844 | 94 | 47 | 48 | 1697 | 1 | `chat gemini-2.5-pro` x94, `execute_tool spotify__show_song` x26, `execute_tool spotify__remove_song_from_playlist` x7, `execute_tool spotify__add_song_to_playlist` x7, `execute_tool spotify__show_playlist_library` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool file_system__show_file`, `execute_tool spotify__create_playlist`, `execute_tool finish`, `Evaluator.Evaluate` |
| 634f342_3 | 1336 | 60 | 30 | 31 | 1240 | 1 | `chat gemini-2.5-pro` x60, `execute_tool spotify__show_song` x16, `execute_tool spotify__remove_song_from_playlist` x6, `execute_tool spotify__show_playlist_library` x3, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool file_system__login`, `execute_tool file_system__show_file`, `execute_tool spotify__create_playlist`, `execute_tool finish`, `Evaluator.Evaluate` |
| 8749218_1 | 1675 | 58 | 29 | 30 | 1582 | 1 | `chat gemini-2.5-pro` x58, `execute_tool spotify__show_recommendations` x6, `execute_tool spotify__add_song_to_playlist` x5, `execute_tool phone__show_text_message` x5, `execute_tool spotify__add_to_queue` x4, `execute_tool spotify__play_music` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool spotify__clear_song_queue`, `execute_tool spotify__create_playlist`, `execute_tool spotify__shuffle_song_queue`, `execute_tool spotify__show_playlist`, `execute_tool finish`, `Evaluator.Evaluate` |
| 8749218_2 | 662 | 20 | 10 | 10 | 628 | 1 | `chat gemini-2.5-pro` x20, `execute_tool spotify__clear_song_queue` x2, `execute_tool spotify__show_recommendations` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool supervisor__show_profile`, `execute_tool spotify__login`, `execute_tool spotify__add_to_queue`, `execute_tool spotify__create_playlist` |
| fd1f8fa_1 | 591 | 18 | 9 | 9 | 560 | 1 | `chat gemini-2.5-pro` x18, `execute_tool supervisor__show_account_passwords` x2, `execute_tool spotify__show_liked_songs` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool spotify__login`, `execute_tool spotify__remove_song_from_queue`, `execute_tool spotify__play_music`, `execute_tool spotify__show_song_queue` |
| fd1f8fa_2 | 367 | 10 | 5 | 5 | 348 | 1 | `chat gemini-2.5-pro` x10, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool spotify__show_song_queue`, `execute_tool supervisor__show_account_passwords`, `execute_tool spotify__login`, `execute_tool spotify__show_liked_songs` |
| fd1f8fa_3 | 1030 | 40 | 20 | 21 | 964 | 1 | `chat gemini-2.5-pro` x40, `execute_tool spotify__remove_song_from_queue` x5, `execute_tool spotify__play_music` x5, `execute_tool spotify__show_liked_songs` x2, `execute_tool spotify__show_current_song` x2, `Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent LiteLLM Tool Calling`, `execute_tool initial_observation`, `execute_tool supervisor__show_account_passwords`, `execute_tool supervisor__show_profile`, `execute_tool spotify__login`, `execute_tool spotify__show_song_queue`, `execute_tool spotify__next_song`, `execute_tool finish`, `Evaluator.Evaluate` |


## Reproducing this report

```sh
python3 reference/gen-12run-report.py /tmp/autobench/run12-ykt3-v135-20261004.json v1.35 "OpenShift — ykt3, single-cluster Helm install, agent runner direct (ETE-ext gateway)" docs/results/v1.35-2026-10-04/12run-ocp.md
```

The run JSON and the mirrored artifacts it points at are produced by `reference/run-12.py`; if `/tmp` has been pruned since, re-hydrate the mirror with `reference/remirror.py` first — the generators treat a missing artifact as an empty one and will quietly emit a much shorter report.
