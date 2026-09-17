# The three benchmarks, for people new to them

We run three benchmarks through the AutoBench Service: **gsm8k**, **tau2**, and **appworld**.
They are not three interchangeable test suites — they form a deliberate difficulty ladder, and each
one stresses a different part of the agent stack. This note explains what each is, what a single
task actually involves, and what they look like in practice.

All the "measured" figures below come from our own **v1.28** runs of 2026-09-15 (282 tasks attempted
across two clusters: OpenShift `ykt3→ykt2` and single-node KinD), not from the benchmarks' published
papers. Those runs are published under
[`docs/results/v1.28-2026-09-15/`](results/v1.28-2026-09-15/), and every figure here is traceable to
their artifacts.

Per-task averages are computed over all **267 task rows** the two matrices produced, **none of which
lost telemetry** — so unlike earlier measurements there is no intact/damaged split to correct for.
The 15 missing rows are all appworld tasks killed by the 600 s per-task timeout, and a killed task
leaves no `report.ndjson` row at all: **pass rates are over the 282 tasks attempted, while token and
latency figures describe the 267 that finished.**

<!-- Regenerate this list: python3 reference/gen_toc.py docs/BENCHMARKS_PRIMER.md -->
<!-- toc -->

**Contents**

- [At a glance](#at-a-glance)
- [gsm8k — the smoke test](#gsm8k--the-smoke-test)
- [tau2 — the conversational one](#tau2--the-conversational-one)
- [appworld — the hard one](#appworld--the-hard-one)
- [What each one ships with, and what we supply](#what-each-one-ships-with-and-what-we-supply)
- [How to read our reports](#how-to-read-our-reports)
- [What a run costs](#what-a-run-costs)
  - [What one leg costs](#what-one-leg-costs)
  - [Model choice, measured on identical tasks](#model-choice-measured-on-identical-tasks)
  - [Read every figure above as a floor](#read-every-figure-above-as-a-floor)
- [Picking a benchmark](#picking-a-benchmark)

<!-- /toc -->

---

## At a glance

| | **gsm8k** | **tau2** | **appworld** |
|---|---|---|---|
| What it tests | multi-step arithmetic reasoning | multi-turn dialogue + tool use | long-horizon app automation |
| Task rows measured | 172 of 172 | 60 of 60 | 35 of 50 (15 timed out) |
| Rows with lost telemetry | 0 | 0 | 0 |
| **Pass rate** | **0.97** | **0.83** | **0.00** |
| Input tokens / task | **341** | **90,902** | **269,953** |
| Output tokens / task | 180 | 2,061 | 25,140 |
| LLM calls / task † | 1.1 | 11.4 | 29.2 |
| Tool calls / task | 1.1 | 11.4 | 14.6 |
| Median task latency | **4.9s** | **84s** | **264s** |
| Slowest task seen | 39s | 136s | 592s |
| Model we use | gpt-5-mini (or gpt-4.1) | claude-sonnet-5 | gemini-2.5-pro |
| Task pool | 8.5K problems (HuggingFace) | 114 (`retail` domain) | grouped scenarios |
| `task_id` format | integer (`0`, `1`, …) | integer (`0`, `1`, …) | `21abae1_1` |

The headline is the **scale gap**: a tau2 task costs ~267× the input tokens of a gsm8k task, and an
appworld task ~792×. Choose accordingly — a 50-task gsm8k run is about a minute; a 20-task appworld
run is 30–45 minutes and millions of tokens.

---

## gsm8k — the smoke test

**What it is.** GSM8K — **"Grade School Math 8K"**, where the *8K* is the dataset size: ~8.5K
problems (7,473 train + 1,319 test). They are grade-school maths word problems needing **2–8
elementary steps** (+ − × ÷); no algebra or geometry. Each has one correct numeric answer, graded by
**exact match**. The difficulty is not the arithmetic but carrying a multi-step chain without
slipping — which is why it became a standard reasoning probe.

**Where the data comes from.** The MCP (`ghcr.io/exgentic/exgentic-mcp-gsm8k`) loads the dataset from
**HuggingFace** at startup. That is why an `hf-secret` must *exist* in the namespace even with an
empty value (the dataset is public) — without it the MCP pod sits in `CreateContainerConfigError` and
the agent crash-loops.

**What one task looks like.** The agent receives a word problem, thinks, then calls a tool to submit
its answer. In our harness a typical task is **one real LLM call and one tool call** — it is close to
the simplest possible agentic loop.

**What it stresses.** Almost nothing about the *platform* — which is exactly why it is useful. If
gsm8k fails, the problem is infrastructure (deploy, auth, LLM reachability, telemetry), not agent
capability. It is our canary.

**What to expect.** ~0.97 pass rate, ~5s per task, ~341 input tokens. Deterministic enough that the
same task produces the *same token count* on different clusters — **6 of the 12 legs in the v1.28 pair
have byte-identical input-token totals across OpenShift and KinD** — and we use that as a correctness
check that two environments are genuinely comparable.

---

## tau2 — the conversational one

**What it is.** τ²-bench evaluates an agent acting as a support agent in a tool-backed domain: it
must hold a **multi-turn conversation** while calling domain APIs to actually accomplish the request.

**Which domain.** τ²-bench ships four — `mock`, `retail`, `airline`, `telecom`. We set **no** subset
override, so we get the library default: **`retail`** (114 tasks). tau2 `task_id`s are that domain's
own ids, so `task_id` 0–19 are the first 20 *retail* tasks. Worth stating explicitly whenever you
quote a tau2 number, because **the domain is not recorded in the artifacts** — it is only inferable
from the absence of an override. Switching domains would change the numbers, and `airline` has only
50 tasks, so `max_tasks` above that would silently cap.

**The key architectural difference.** tau2 introduces a **second LLM — a user simulator** that plays
the customer. So each task involves two models talking to each other, plus tool calls. That single
fact explains most of tau2's profile: ~11 LLM calls and ~11 tool calls per task, and ~91k input
tokens because the whole growing conversation is re-sent on every turn.

**What it stresses.** Conversation state, tool selection over many turns, and the agent's ability to
stay on task. It is also the first benchmark where **model choice dominates the result** — see below.

**What to expect.** ~0.83 pass rate with claude-sonnet-5 and ~84s median per task, in a fairly narrow
band — the fastest task in the v1.28 pair took 44s and the slowest 136s, so tau2 does not have the
long tail appworld does. Results are inherently a little noisy run-to-run because episodes are
nondeterministic: inside that single pair the same 10 tasks scored **0.90 on one cluster and 1.00 on
the other**, and the same 20 tasks 0.75 vs 0.80. A 0.05–0.10 gap across a 10–20 task run is normal
variance, not a regression.

> **Model sensitivity is dramatic here.** On the same 10 tasks, tau2 scored **0.1 with gpt-5-mini**
> and **0.9 with claude-sonnet-5** (a one-off comparison from an earlier image, not part of the v1.28
> pair — the model has been pinned ever since, so no current matrix re-measures it). That is not a
> small tuning difference — it is the benchmark being
> effectively unsolvable for one model and mostly solved by another. The Service therefore pins tau2
> to claude-sonnet-5 via a `model_override`. If you see a tau2 number, check which model produced it
> before comparing anything.

---

## appworld — the hard one

**What it is.** AppWorld tasks are realistic everyday digital chores carried out across a suite of
simulated everyday apps via their APIs — the kind of request that requires discovering the right
APIs, chaining many calls, and handling intermediate state.

**Task ids look different.** appworld groups several related tasks under one base scenario/"world",
so ids look like `21abae1_1`, `21abae1_2`, `21abae1_3` — a scenario hash plus a sub-task number. Each
still runs as an independent session.

**What it stresses.** Long-horizon planning and composition. ~29 LLM calls and ~15 tool calls per
task, ~270k input tokens, and a median of **4.4 minutes per task** — with a real tail: the slowest
task that finished took 592s, and **15 of the 50 attempted tasks hit the 600 s per-task timeout** and
so left no report row at all.

**Expect a pass rate of 0.0 — and that is the honest result.** We run appworld with a *generic*
`tool_calling` agent, which is not specialised for it. 0.0 does not mean the platform is broken; the
runs complete cleanly, tasks execute, tokens are recorded, evaluation simply says the agent did not
accomplish the goal — and it is not giving up early either: **34 of the 35 tasks that finished called
the `finish` tool**, so the agent believed it was done. appworld's role in our matrix is as a **stress
test of the pipeline at scale** (long tasks, big contexts, real timeouts) rather than a capability
score we expect to move.

---

## What each one ships with, and what we supply

A benchmark is not something the Service configures — it is a **container image**. Each benchmark's
dataset (or world), its task list and its evaluator all live inside one MCP tool image, and the only
thing we add from outside is a credential or two plus a per-benchmark quirk override:

| | what's baked in | `tool_env` it needs |
|---|---|---|
| **gsm8k** | the HuggingFace dataset loader | `HF_TOKEN` (from `hf-secret`), plus `EXGENTIC_SET_BENCHMARK_RUNNER=direct` |
| **tau2** | the τ²-bench library + `retail` domain, and a user-simulator LLM | `OPENAI_API_KEY` + `EXGENTIC_SET_BENCHMARK_ACTION_TIMEOUT=1000` — it makes its own inference calls |
| **appworld** | the whole app-suite sandbox (`exgentic install --benchmark appworld`) | just `BENCHMARK_NAME` — upstream's `.env.appworld` is explicitly empty |

Two things follow that are easy to miss:

- **There is no knob for the dataset or the domain.** tau2 runs `retail` because that is the
  library's default inside the image, not because we selected it; changing it would mean an
  override we deliberately don't set. Same for gsm8k's problem pool.
- **The subject under test is the same binary for all three.** One agent image
  (`exgentic-a2a-tool_calling:latest`) is used for every benchmark, gaining only a `-<benchmark>`
  name suffix — which is exactly why appworld's 0.0 is an honest result and not a misconfiguration:
  the agent has nothing appworld-specific in it.

The deployment-side detail (which secret, which env var, what breaks without it) is in
[`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md) §3.3–§3.4.

---

## How to read our reports

A few things that trip people up:

- **`pass_rate` = `evaluated_pass / total`.** A task that errors before evaluation counts as not
  passed, so a low pass rate can mean "failed the task" *or* "never got to be judged".
- **† The `llm` column changed meaning between agent versions, so never compare across it.** The
  table above is measured on `exgentic 0.3.5.dev146` and its `llm` figures count real calls
  **one-for-one, with no offset to subtract**. Agents up to `0.3.5.dev131` issued a `max_tokens=1`
  capability probe before the real work and counted it as a `chat` span, so ***their* gsm8k `llm=2`
  means one real call** — subtract one per task before comparing an older number with these. As of
  `0.3.5.dev145` the probe is *replaced* by an unbilled `GET /v1/models` reachability check that emits
  no `chat` span, verified absent across all **1,895 `chat` spans** of this v1.28 pair (1,122
  OpenShift + 773 KinD, all four model classes): not one carries `request_max_tokens = 1`. (That
  replacement had a sharp edge of its own — it ran per task with a hard 10 s cap and no retry, and on
  a high-latency gateway it failed tasks outright, killing 12 of 141 KinD tasks on `dev145`. Fixed in
  `dev146`, which these runs used, and both matrices record **zero** probe failures. See Bug 3 in
  `docs/exgentic-agent-bug-report-20260901.md`.) The practical trap: **do not compare `llm` counts
  across runs that straddle the change** — a leg will look like it made one fewer call per task when
  only the instrumentation changed.
- **Input tokens grow faster than output.** Every LLM call re-sends the whole conversation, so
  cumulative input scales roughly with the square of the turn count while output is bounded per
  call. That is why tau2/appworld input *totals* dwarf output.
- **But output is usually the more *variable* direction** — OUT CV > IN CV in **15 of the 22 v1.28
  legs that ran more than one task**. This corrects an earlier claim here that input variance is
  always wider; it is not. On single-turn gsm8k the prompt is near-constant (IN CV 0.06–0.09 on the
  gpt-5-mini legs that ran clean) while answer length swings with how much the model reasons (OUT CV
  0.52–0.86), so output varies ~8× more in relative terms. **Within one benchmark the model decides
  the shape**: the gpt-4.1 leg needs ~3 tool round-trips per task, so its input varies too (IN CV
  0.39–0.44 on the same five tasks) — which is why the comparison report ranges CVs per model, not
  per benchmark. **One errored task is enough to break that
  IN range**: OCP leg #6 (`ibac-only`, 2 of 5 tasks errored) carries a row with `llm_input_tokens = 0`
  from a task that failed before its first model call, which lifts that leg alone to IN CV 0.51. That
  is the legitimate zero — not the telemetry bug two bullets down — so read the error count beside a
  CV before believing it. Only long-horizon **appworld** inverts it in every leg
  (IN CV 0.15–0.69 against OUT CV 0.13–0.52), because its tasks differ enormously in turn count and
  compounding context then dominates. Don't assume a direction — check the CV columns.
- **Task selection is deterministic.** A run takes the first `max_tasks` tasks, so the same
  `task_id` is the same task across runs and clusters, and a smaller run's tasks are a prefix of a
  larger one's. Cross-run comparisons on the same benchmark are therefore like-for-like.
- **Implausibly small token counts are a telemetry bug, not a cheap task — and `== 0` is the wrong
  test.** Use the structural test: **`llm` ≤ 1 alongside `tool` ≥ 2 is impossible**, since each tool
  call needs a preceding model turn. `llm=1` next to `tool=11` is a lost span. A second impossible
  shape is a task with `status = OK` and `llm = 0` — nothing completes without a model turn (it has
  to be gated on status, because an appworld task that fails before its first call legitimately has
  no `chat` span). Affected runs' **token totals are understated while their pass rates remain
  valid** — the tasks really ran and were really judged.

  Why not `== 0`: while the `max_tokens=1` probe existed, it was the span that *survived* the loss,
  and it carried its own usage on non-reasoning models — gpt-5-mini rejected it and left the familiar
  `in=0, out=0`, but claude-sonnet-5 left `in=8, out=1` and gemini-2.5-pro `in=1, out=0`. A
  zero-check therefore missed every tau2 and appworld case. Now that the probe is gone a damaged task
  drops to zero `chat` spans, so `llm = 0` catches more than it used to — but the structural pair
  test is the one that holds across agent versions, and it is what the report generators use.
  **This defect is fixed as of `0.3.5.dev145`** — a warm agent now keeps full token attribution,
  measured over four reuse legs. We keep deploying fresh per run anyway, but for an unrelated reason
  (a newly created pod re-pulls `:latest`; a long-lived one serves a stale digest indefinitely).
  Details: `docs/exgentic-agent-bug-report-20260901.md`.
- **The LLM gateway caches completions, so a repeated prompt can be a replay.** Sending the same
  request body twice within the gateway's TTL returns the *same response `id`* and the same `usage` —
  proven by hand against both gateways, with the agent out of the picture. **The TTL is ~10 minutes**,
  measured by survival curve (one probe per nonce at its own age: HIT at 3/5/7/9 min, MISS at
  11/13/15/18/21). A replay re-reports the stored token counts and its latency measures a cache
  lookup, so **per-call latency and output tokens are not independent across legs that share
  prompts** — which legs #1–#3 and #5–#8 of the canonical matrix do, since they all open with the same
  five gsm8k tasks. Input tokens and pass rates are unaffected. This is not an agent setting we can
  turn off (`EXGENTIC_LITELLM_CACHING=false` is pinned and changes nothing), and **latency is the
  wrong detector** — one measured replay took 3.0 s, the same as a miss. Compare response `id`s. To
  get independent legs, space them: `BM_CACHE_GAP` in `reference/run-12.py` rests each (benchmark,
  model) prompt set for 900 s, which is what the v1.28 matrices were run with.

Three more that apply specifically to the **latency** columns, all measured in the plugin-overhead
study ([docs/PLUGIN_OVERHEAD.md](PLUGIN_OVERHEAD.md)):

- **Warm-up is exactly the first concurrency wave** — the first `num_parallel` tasks — not "the first
  few tasks". Splitting the no-sidecar baseline legs at the wave gives a penalty that is consistent
  within a cluster — 2.46/2.05× on OpenShift, 8.38/22.55× on KinD, whose ~0.1 s steady state makes the
  same startup cost a bigger multiple; splitting at a fixed 10 tasks instead gives 1.18/1.22× and
  1.52/**0.94**× — the last one below 1, reporting warm-up as a speed-up. The consequence for small
  runs is blunt: at `p=4`, a
  5-task leg spends four of its five tasks inside the transient, so its per-task average is mostly
  measuring startup.
- **The deploy is the unit of replication, not the task.** Every task in a leg shares one deployment,
  so a per-task interval answers "how variable are tasks within this deploy?" — not "how variable is
  this configuration?". The real noise floor is the spread between two independent deploys of the
  same condition, and on both clusters it exceeded every effect we were trying to resolve except the
  single dominant one. Adding tasks tightens the wrong interval; add deploys.
- **Absolute latencies are not portable across clusters.** The same condition on the same image
  measured 4–93× apart on our two clusters, and the two disagreed about *which* component the cost
  belonged to. Pass rates and token counts do travel; seconds do not. Always name the cluster.

## What a run costs

The [at-a-glance](#at-a-glance) table gives the raw per-task counts. These are the *derived* figures
you need to budget with — same 267 rows, both platforms pooled:

| per task | gsm8k | tau2 | appworld |
|---|---:|---:|---:|
| **total tokens** | **520** | **92,963** | **295,093** |
| × a gsm8k task | 1× | 179× | 567× |
| input share of tokens | 66% | 98% | 92% |
| share of task time inside model calls | 90% | 58% | 96% |
| tokens per **passed** task | 537 | ~112 K | no finite value |

Two of those rows carry most of the practical advice. **The input share** says where the money goes:
for tau2 and appworld the bill essentially *is* the input side, so the cost driver is turn count and
context compounding — not how verbose the model is — and a cheaper-input model beats a terser one.
**Tokens per passed task** (per-task cost ÷ pass rate) is the honest unit when you are comparing
options rather than sizing a run: a model that halves your token use and halves your pass rate has
gained you nothing, and appworld at 0.00 has no finite cost per success at all.

### What one leg costs

Measured totals from the v1.28 legs, so you can size a run before starting it:

| leg | tokens (OpenShift) | tokens (KinD) |
|---|---:|---:|
| gsm8k, 1 task | 470 | 790 |
| gsm8k, 10 tasks | 5.1 K | 5.1 K |
| gsm8k, 50 tasks at `p=4` | 25 K | 24 K |
| tau2, 10 tasks | 1.01 M | 1.04 M |
| tau2, 20 tasks at `p=4` | 1.74 M | 1.78 M |
| appworld, 5 tasks | 1.49 M | 0.93 M |
| appworld, 20 tasks at `p=4` | 5.71 M | 2.20 M |
| **the whole 12-run matrix** | **10.0 M** | **6.0 M** |

**Budget by benchmark, not by task count.** All eight gsm8k legs together are **0.4%** of the
matrix's token bill (0.8% on KinD); appworld's two legs are **72%** of it (52% on KinD). A 50-task
gsm8k leg costs less than a single appworld *task*. And the same request body is not the same bill on
two clusters — the 20-task appworld leg cost 2.6× more on OpenShift, because appworld turn counts are
nondeterministic and the slower cluster's tasks ran longer before the 600 s timeout hit them.

### Model choice, measured on identical tasks

Two legs of the matrix ran the **same five gsm8k tasks** at `p=4` and differed only in model, which
makes them a clean comparison:

| same 5 gsm8k tasks | gpt-4.1 | gpt-5-mini |
|---|---:|---:|
| pass rate (OpenShift / KinD) | 0.80 / 1.00 | 1.00 / 1.00 |
| LLM calls per task | 2.8 – 3.0 | 1.0 |
| input tokens per task | 775 – 837 | 313 |
| output tokens per task | 57 – 63 | 137 – 355 |
| total tokens per task | 832 – 900 | 450 – 668 |
| median task latency | 10.4 – 10.8 s | 11.2 – 16.4 s |

The reasoning model answers in **one** call; gpt-4.1 needs ~3 tool round-trips, so it sends 2.6× the
input and emits roughly a quarter of the output. Note that it is also the *faster* of the two per
task despite tripling the calls — reasoning time is not free.

**So the token ranking and the money ranking disagree, and which one wins is a property of your price
list.** Equating the two bills — `in × P_in + out × P_out` — solves for break-even at
`P_out / P_in ≈ 2.7`; the two platforms bracket it at 1.8 and 5.8, because gpt-5-mini's output length
swings with how much it reasons. Priced above that ratio gpt-4.1 is cheaper, below it gpt-5-mini. We
publish token counts and no prices, deliberately: our gateway does not bill us, so any dollar figure
here would be someone else's rate card.

```
cost per task  =  (input_tokens × P_in  +  output_tokens × P_out) / 1e6
```

### Read every figure above as a floor

- **A killed task still costs.** The 600 s per-task timeout leaves no `report.ndjson` row, so
  appworld's 15 timed-out tasks contributed tokens that appear in none of these totals.
- **tau2's user simulator is invisible to us.** It runs inside the **MCP** pod, which is not
  instrumented, so its inference is billed by the gateway and counted nowhere here. The tell is in
  the latency split: 27% of a tau2 task's wall time sits *inside tool calls*, against under 1% for
  gsm8k and appworld, and every `chat` span we record carries the agent's model.
- **A cache replay can also make a leg read high.** Legs that share a prompt set within the
  gateway's ~10 min TTL re-report stored `usage` for calls that were never made upstream.
- **Plugins add billed calls of their own** — one IBAC judge completion per authorized tool call,
  outside the agent's spans. See [`PLUGIN_OVERHEAD.md`](PLUGIN_OVERHEAD.md).

---

## Picking a benchmark

| If you want to… | use |
|---|---|
| check a cluster/deploy/auth/telemetry path works | **gsm8k**, 1–10 tasks |
| exercise concurrency and volume cheaply | **gsm8k**, 50 tasks at `max_parallel_sessions=4` |
| compare models meaningfully | **tau2** (it discriminates; gsm8k saturates at ~1.0) |
| stress long contexts, long tasks, timeouts | **appworld** |
| get a fast signal that nothing regressed | **gsm8k** — if it fails, stop and fix infrastructure |
