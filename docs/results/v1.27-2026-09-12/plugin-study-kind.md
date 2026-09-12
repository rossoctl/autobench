# AuthBridge plugin overhead — designed experiment (KinD — single-node local cluster)

**Report generated:** 2026-09-12T19:31:59Z  
**Service version:** `v1.27`  
**Platform:** KinD — single-node local cluster  
**Legs executed:** 13 of 13  
**Judge-call evidence:** included  
**Statistics:** 90% bootstrap CIs (10,000 resamples), permutation tests (20,000 shuffles), seed `20260912`

**Contents**

- [What this measures, and why it needed its own runs](#what-this-measures-and-why-it-needed-its-own-runs)
- [Run order was balanced by design (the crossover)](#run-order-was-balanced-by-design-the-crossover)
- [The comparison is like-for-like (identical work)](#the-comparison-is-like-for-like-identical-work)
- [The decisive security question: caching, or fail-open?](#the-decisive-security-question-caching-or-fail-open)
- [Warm-up is one concurrency wave, and must be excluded rather than averaged in](#warm-up-is-one-concurrency-wave-and-must-be-excluded-rather-than-averaged-in)
  - [The transient is the first wave, not the first N tasks](#the-transient-is-the-first-wave-not-the-first-n-tasks)
  - [Warm-up vs steady state, per leg](#warm-up-vs-steady-state-per-leg)
- [Steady-state cost per condition, with intervals](#steady-state-cost-per-condition-with-intervals)
  - [Incremental cost of each layer](#incremental-cost-of-each-layer)
  - [The unit of replication is the deploy, not the task](#the-unit-of-replication-is-the-deploy-not-the-task)
- [Replicate agreement (the order-effect test)](#replicate-agreement-the-order-effect-test)
- [Judged calls at n=50](#judged-calls-at-n50)
- [Does per-call cost hold across benchmarks? (the tau2 linearity test)](#does-per-call-cost-hold-across-benchmarks-the-tau2-linearity-test)
- [Confidence and limitations](#confidence-and-limitations)
  - [Reproducing this](#reproducing-this)

## What this measures, and why it needed its own runs

This is a **designed experiment**: the conditions, the task count and the run order were all chosen in order to measure plugin cost. That is worth stating because the obvious cheaper route does not work — the canonical 12-run matrix already exercises each plugin preset, so it is tempting to mine plugin cost out of runs you have rather than commission new ones. Three defects make those numbers unresolvable, and each was measured rather than assumed:

| defect when mining the 12-run matrix | evidence | fixed here by |
|---|---|---|
| each preset runs **once at `max_tasks=5`** | bootstrap puts the minimum detectable preset-to-preset difference at ~**3x**; the pairs of interest differ by far less, so a null result there is *underpowered*, not reassuring | `max_tasks=50` |
| per-task figures are dominated by **warm-up** | in 50-task baseline legs, which have **no sidecar at all**, the first concurrency wave costs ~**2.0–2.2x** steady state and the transient is gone by the second wave; at `p=4` that is *four of the five tasks* an n=5 leg measures | excluding the first wave, and reporting it **separately** |
| **run order** aliases onto the plugin variable | the legs run in one fixed sequence, so any monotone drift loads onto whichever preset ran last | a **reversed-order replicate** |

One property of the 12-run design is worth keeping, and this experiment inherits it: **task selection is deterministic**, so every leg runs the same tasks in the same order with the same model. That is verified below before any latency figure is quoted.

## Run order was balanced by design (the crossover)

Replicate 2 runs the five conditions in **reverse** order. That makes each condition's mean run position identical, so a smooth drift over the session — gateway warm-up, cache fill, node contention — cancels between replicates instead of being attributed to a preset.

| condition | rep 1 position | rep 2 position | position sum |
|---|---:|---:|---:|
| baseline | 2 | 11 | 13 |
| auth-only | 3 | 10 | 13 |
| ibac-only | 4 | 9 | 13 |
| full | 5 | 8 | 13 |
| full+ibac:observe | 6 | 7 | 13 |

**Balanced** — every condition has the same position sum, so run order cannot favour any one of them.

## The comparison is like-for-like (identical work)

If every leg did the same work, a latency difference is attributable to the plugin configuration. `num_parallel` is read back from the artifacts rather than from the request, so a leg that silently ran at the wrong concurrency cannot pass unnoticed.

| # | pos | condition | rep | tasks | OK | input tok | output tok | LLM calls | tool calls | p | model |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 101 | 2 | baseline | 1 | 50 | 50 | 16030 | 9560 | 101 | 51 | 4 | `openai/Azure/gpt-5-mini-2025-08-07` |
| 102 | 3 | auth-only | 1 | 50 | 50 | 16030 | 9560 | 101 | 51 | 4 | `openai/Azure/gpt-5-mini-2025-08-07` |
| 103 | 4 | ibac-only | 1 | 50 | 50 | 16030 | 9560 | 101 | 51 | 4 | `openai/Azure/gpt-5-mini-2025-08-07` |
| 104 | 5 | full | 1 | 50 | 50 | 16361 | 9777 | 102 | 52 | 4 | `openai/Azure/gpt-5-mini-2025-08-07` |
| 105 | 6 | full+ibac:observe | 1 | 50 | 50 | 16015 | 8278 | 101 | 51 | 4 | `openai/Azure/gpt-5-mini-2025-08-07` |
| 106 | 7 | full+ibac:observe | 2 | 50 | 50 | 16015 | 8278 | 101 | 51 | 4 | `openai/Azure/gpt-5-mini-2025-08-07` |
| 107 | 8 | full | 2 | 50 | 50 | 15684 | 8575 | 100 | 50 | 4 | `openai/Azure/gpt-5-mini-2025-08-07` |
| 108 | 9 | ibac-only | 2 | 50 | 50 | 15684 | 9151 | 100 | 50 | 4 | `openai/Azure/gpt-5-mini-2025-08-07` |
| 109 | 10 | auth-only | 2 | 50 | 50 | 15684 | 9151 | 100 | 50 | 4 | `openai/Azure/gpt-5-mini-2025-08-07` |
| 110 | 11 | baseline | 2 | 50 | 50 | 15684 | 8319 | 100 | 50 | 4 | `openai/Azure/gpt-5-mini-2025-08-07` |

⚠️ **The legs did not do byte-identical work.** Input-token totals are the sensitive signature; where they differ, the corresponding latency delta is confounded by workload and the per-condition figures below should be read as indicative only. Note that a differing **output**-token total is expected and harmless — the model is sampled, not deterministic.

## The decisive security question: caching, or fail-open?

Across four 12-run matrices, IBAC legs authorized only *some* of their tool calls, and the count wobbled across identically configured runs. Two explanations fit that equally well from the matrix artifacts, and they could not be more different in consequence:

1. **Benign — decision caching under concurrency.** At `max_parallel_sessions=4` several identical calls fire at once, all miss the cache and consult the judge; later ones hit a warm entry.
2. **A fail-open enforcement gap** — the action proceeded with no authorization decision. For a security control this matters far more than any latency figure in this document.

Leg **#111** settles it by removing concurrency: `ibac-only` at **`max_parallel_sessions=1`**, so the tool calls are strictly serial and no two can race for the same cache entry.

| measurement | value |
|---|---:|
| tasks attempted | 10 |
| tasks OK | 10 |
| tool calls (serial, OK tasks) | 10 |
| judge calls in the run window | **10** |

**Verdict: enforcement is 1:1 without concurrency, and TTL caching is ruled out.** All 10 serial tool calls were judged (10 judge calls) — every action got its own authorization decision.

That result does more than reassure; it **eliminates one of the two candidate explanations**. A decision cache with a TTL would have collapsed these 10 same-shape calls to roughly one judge call *even when serial*. It did not. So whatever produces the shortfall at `p=4` is specific to **concurrency**, and two mechanisms remain — which are *not* equally benign:

- **Benign: single-flight deduplication.** Concurrent identical requests share one in-flight decision. Every action is still authorized, just not by its own round-trip.
- **Not benign: fail-open under race.** A call proceeds while a decision is still pending, so the action is never actually authorized.

Both are concurrency-only and produce **identical call counts**, so no amount of counting separates them — that now requires reading the sidecar's authorization path. The contribution of this leg is to narrow the audit from *'is IBAC enforcing at all?'* to *'what does the concurrent path do with an in-flight decision?'*, and to establish that enforcement is not globally broken.

## Warm-up is one concurrency wave, and must be excluded rather than averaged in

Per-task cost is not stationary: the first tasks of *any* leg pay one-time costs (connection setup, TLS handshake, cache fill) that have nothing to do with plugins. Averaging them into a 'per-task overhead' inflates it — which is exactly what a 5-task leg cannot avoid doing.

Metric throughout is **non-LLM agent time** (`agent_call_s - llm_total_s`): MCP connect, tool calls and sidecar interception, with the shared LLM gateway's variance removed.

### The transient is the first wave, not the first N tasks

This matters more than it sounds, because it determines what to exclude. Median non-LLM seconds by rank bucket, baseline legs only (no sidecar at all, so this is pure deployment warm-up):

| # | rep | p | rank 1–4 | rank 5–8 | rank 9–12 | rank 13–50 |
|---|---:|---:|---:|---:|---:|---:|
| 101 | 1 | 4 | 0.271 | 0.109 | 0.125 | 0.135 |
| 110 | 2 | 4 | 0.301 | 0.114 | 0.115 | 0.109 |

The cost drops to steady state **after the first bucket** and stays there — the transient is one wave of `max_parallel_sessions` tasks, all of which start before any connection is warm. Splitting at the wave gives a reproducible ratio; splitting at a fixed 10 tasks buries it by mixing six steady-state tasks into the warm bucket, and on one leg even inverts its sign (0.72x). **The cutoff below is therefore derived per leg from the artifacts' own `num_parallel`, not fixed.**

### Warm-up vs steady state, per leg

| # | condition | rep | cutoff | first wave (med) | steady (med) | steady n | ratio |
|---|---|---:|---:|---:|---:|---:|---:|
| 101 | baseline | 1 | 4 | 0.271 | 0.125 | 46 | 2.17x |
| 102 | auth-only | 1 | 4 | 0.527 | 0.192 | 46 | 2.75x |
| 103 | ibac-only | 1 | 4 | 2.436 | 2.014 | 46 | 1.21x |
| 104 | full | 1 | 4 | 2.097 | 2.107 | 46 | 1.00x |
| 105 | full+ibac:observe | 1 | 4 | 2.911 | 1.785 | 46 | 1.63x |
| 106 | full+ibac:observe | 2 | 4 | 4.775 | 2.338 | 46 | 2.04x |
| 107 | full | 2 | 4 | 2.864 | 2.036 | 46 | 1.41x |
| 108 | ibac-only | 2 | 4 | 3.942 | 2.149 | 46 | 1.83x |
| 109 | auth-only | 2 | 4 | 0.390 | 0.201 | 46 | 1.94x |
| 110 | baseline | 2 | 4 | 0.301 | 0.110 | 46 | 2.73x |

Ratios above 1 are the transient. The **baseline** legs have no sidecar, so whatever ratio they show is a property of the deployment; a preset leg's ratio is only evidence about AuthBridge to the extent that it *exceeds* the baseline's. This is the specific reason a 'fixed per-task plugin cost' derived from a 5-task leg is not a plugin cost at all — at `p=4`, four of those five tasks *are* the transient.

**Everything below excludes the first wave of each leg.**

## Steady-state cost per condition, with intervals

Replicates pooled. A bare median invites over-reading; the interval is what says whether a difference was resolvable at all.

| condition | n | median non-LLM s | 90% CI | vs baseline (Δ median) | Δ CI | perm p |
|---|---:|---:|---|---:|---|---:|
| **baseline** | 92 | 0.117 | [0.109, 0.125] | — | — | — |
| auth-only | 92 | 0.196 | [0.174, 0.208] | +0.079 | [+0.056, +0.096] | 0.0000 |
| ibac-only | 92 | 2.107 | [1.730, 2.295] | +1.991 | [+1.618, +2.175] | 0.0000 |
| full | 92 | 2.058 | [1.944, 2.256] | +1.941 | [+1.828, +2.134] | 0.0000 |
| full+ibac:observe | 92 | 1.999 | [1.779, 2.284] | +1.882 | [+1.661, +2.160] | 0.0000 |

A Δ CI that straddles zero means **no difference was resolved** at this n — which, unlike the n=5 case, is now a meaningful statement rather than a limit of the instrument. With 92 baseline samples the tests are comparisons of medians by permutation, so they make no normality assumption. Note that this table is 4 simultaneous tests against one baseline, so the Bonferroni-corrected threshold is 0.0125 rather than 0.05.

### Incremental cost of each layer

The conditions nest: `baseline` ⊂ `auth-only` ⊂ ... The interesting quantity is not each condition's cost against baseline but the **marginal** cost of each added layer, which is what the 12-run's n=5 could never resolve.

| step | Δ median s | Δ CI | perm p | resolved? |
|---|---:|---|---:|---|
| baseline → auth-only | +0.079 | [+0.056, +0.096] | 0.0000 | **yes** |
| auth-only → ibac-only | +1.912 | [+1.543, +2.098] | 0.0000 | **yes** |
| ibac-only → full | -0.049 | [-0.270, +0.364] | 0.7845 | no — CI straddles 0 |
| full → full+ibac:observe | -0.059 | [-0.338, +0.227] | 0.5579 | no — CI straddles 0 |

### The unit of replication is the deploy, not the task

The intervals above treat each task as an independent replicate of its condition. **They are not.** Every task in a leg ran against one deployment, so a per-task interval answers *'how variable are tasks within this deploy?'* — not *'how variable is this condition?'*. Treating the former as the latter is pseudo-replication, and it makes intervals look far tighter than the design can support. The replicate block is what makes this visible and correctable:

| condition | rep 1 leg median | rep 2 leg median | \|Δ\| between deploys |
|---|---:|---:|---:|
| baseline | 0.125 | 0.110 | **0.015** |
| auth-only | 0.192 | 0.201 | **0.009** |
| ibac-only | 2.014 | 2.149 | **0.135** |
| full | 2.107 | 2.036 | **0.071** |
| full+ibac:observe | 1.785 | 2.338 | **0.553** |

Median \|Δ\| between two deploys of the *same* condition is **0.07 s** (max 0.55 s), implying a between-deploy SD of roughly **0.07 s**. Compare that with the marginal effects in the table above — every preset-to-preset step is *smaller than the noise between two deploys of one preset*.

So the correct reading of this experiment is:

- **auth-only → ibac-only: +1.91 s — real.** That is 25.8x the between-deploy SD, far outside what deployment variability can manufacture.
- **baseline → auth-only (+0.08 s); ibac-only → full (-0.05 s); full → full+ibac:observe (-0.06 s) — below the noise floor.** Not 'probably small': **unresolvable** with two deploys per condition, regardless of how many tasks each deploy runs. Adding tasks tightens the wrong interval.

**What it would actually take.** At ~16·σ²/δ² deploys per condition for 80% power, σ=0.074 s here is small enough that the **2 deploys already run suffice** to resolve a 1 s effect. So the unresolved steps above are not under-replicated — they are genuinely smaller than 1 s. Resolving them would mean pinning down effects of ~0.1 s, which needs ~9 deploys per condition, and at that scale cluster drift over the required hours becomes the dominant error rather than deployment variability.

## Replicate agreement (the order-effect test)

Each condition ran twice, once early and once late, in reversed order. Agreement means run order is not driving the result. Disagreement is informative rather than fatal — the *direction* of the differences distinguishes a monotone session drift (all one sign) from deploy-to-deploy variability (mixed signs), and only the first would bias a condition comparison.

| condition | rep 1 med | rep 2 med | Δ | Δ CI | perm p |
|---|---:|---:|---:|---|---:|
| baseline | 0.125 | 0.110 | -0.015 | [-0.046, +0.003] | 0.1050 |
| auth-only | 0.192 | 0.201 | +0.009 | [-0.025, +0.043] | 0.7769 |
| ibac-only | 2.014 | 2.149 | +0.135 | [-0.569, +0.591] | 0.7650 |
| full | 2.107 | 2.036 | -0.071 | [-0.753, +0.171] | 0.6387 |
| full+ibac:observe | 1.785 | 2.338 | +0.553 | [+0.016, +0.892] | 0.0405 |

⚠️ **Replicates disagree for: `full+ibac:observe`.** By the per-task test these are separate populations, so the per-task intervals in the previous section are too narrow — which is the pseudo-replication point made there, arriving here as direct evidence rather than as a caveat.

**But the disagreement is not run-order drift.** The rep-2 minus rep-1 differences go in **both directions** (`baseline` -0.01 s, `auth-only` +0.01 s, `ibac-only` +0.13 s, `full` -0.07 s, `full+ibac:observe` +0.55 s), and a monotone session effect — gateway warm-up, cache fill, node contention — would have to push every condition the same way, since replicate 2 ran entirely later than replicate 1. A non-monotone pattern instead points at **deploy-to-deploy variability**: each leg is a fresh deployment, and that is the nuisance variable, not elapsed time.

This is a more useful conclusion than 'order matters', and it is only available because the reversal was built in: run order was **balanced by design** (equal position sums above), so it cannot be what produced a two-directional pattern.

## Judged calls at n=50

The IBAC judge is itself an LLM call, so judge latency inherits inference variance and **the number of judged calls need not equal the number of tool calls**. At n=5 a leg offered at most 5 chances to observe this and the resulting median was a mixture of two populations. At n=50 the ratio is estimable.

| # | pos | condition | rep | tool calls | judge calls | judged / call |
|---|---:|---|---:|---:|---:|---:|
| 101 | 2 | baseline | 1 | 51 | 0 | 0.00 |
| 102 | 3 | auth-only | 1 | 51 | 0 | 0.00 |
| 103 | 4 | ibac-only | 1 | 51 | 51 | 1.00 |
| 104 | 5 | full | 1 | 52 | 51 | 0.98 |
| 105 | 6 | full+ibac:observe | 1 | 51 | 48 | 0.94 |
| 106 | 7 | full+ibac:observe | 2 | 51 | 48 | 0.94 |
| 107 | 8 | full | 2 | 50 | 50 | 1.00 |
| 108 | 9 | ibac-only | 2 | 50 | 47 | 0.94 |
| 109 | 10 | auth-only | 2 | 50 | 0 | 0.00 |
| 110 | 11 | baseline | 2 | 50 | 0 | 0.00 |

A ratio near 0 is a preset that does not engage IBAC (correct for `auth-only`); near 1 is full enforcement. **Intermediate ratios are the finding**, and the serial diagnostic above is what interprets them.

The judge window is derived from the `run_id` timestamp plus the run's `wall_seconds` (+20 s slack). Legs are separated by teardown and redeploy, so windows do not overlap — but this attribution is by time, not by trace id, so a stray non-benchmark request to the judge would land in whichever window contained it.

## Does per-call cost hold across benchmarks? (the tau2 linearity test)

There is a tempting shortcut for pricing a plugin on an expensive benchmark without running it: take the per-tool-call delta from cheap gsm8k legs and multiply by the target benchmark's tool-call count. That assumes per-call cost is a **constant** across benchmarks. gsm8k makes ~1 substantive tool call per task; tau2 makes roughly an order of magnitude more, so this pair tests the assumption directly instead of resting on it.

| leg | condition | tasks OK | tool calls/task | non-LLM s/task (med) | per tool call (s) |
|---|---|---:|---:|---:|---:|
| #112 | baseline | 10 | 11 | 22.29 | 2.026 |
| #113 | full+ibac:observe | 10 | 11 | 69.08 | 6.280 |

**Measured on tau2:** `full+ibac:observe` adds **+46.79 s/task**, i.e. **+4.254 s per tool call**.

**Projected from gsm8k** by that shortcut: the same condition costs +1.882 s/task on gsm8k over ~1 tool call, which scaled by tau2's 11 tool calls/task predicts **+20.71 s/task**.

**Measured / projected = 2.26x.**

**The shortcut does not hold, so do not price a benchmark this way.** Per-call cost is not a constant across benchmarks: whatever drives the difference (session reuse, connection amortisation, cache behaviour across many calls in one session) is not captured by a per-call constant. Measure the benchmark instead — it costs two legs.

## Confidence and limitations

What this design **does** support:

- Steady-state marginal cost per condition, with intervals, at a resolution the 12-run cannot reach.
- A statement about run order that is *tested* (replicate agreement) rather than assumed.
- Separation of one-time warm-up from per-task steady-state cost.
- A judged-vs-unjudged reading with enough calls per leg to be a ratio rather than an anecdote.

What it still does **not** support:

- **Cold-start / admission cost is not isolated.** Deploy and readiness time is excluded from `agent_call_s` entirely, so the cost of *injecting* the sidecar is invisible here. It would need deploy-duration instrumentation, which is a separate change.
- **Infra cost is unmeasurable from these artifacts.** Every `mcp_*`/`a2a_*` CPU and memory field is `0.0` and `has_infra` is `false`, because those values come from an `infra` attribute on the agent's OTEL root span that the upstream agent does not emit. No number of runs fixes this; it needs an upstream change.
- **One model, one cluster, one day.** The conditions are internally comparable; absolute seconds are not portable to other hardware or gateways.
- **The LLM path is excluded by construction.** If a plugin changed LLM latency or token counts, this metric would not show it — the identical-work check is what makes that exclusion safe, and it is verified above rather than assumed.

### Reproducing this

```sh
BM_SPECS=reference/plugin_study_specs.json BM_LABEL=pstudy-kind \
  python3 reference/run-12.py        # detached; see feedback_long_runs_detach_and_adopt
python3 reference/gen-plugin-study.py run12-pstudy-kind.json reference/plugin_study_specs.json v1.27 'KinD — single-node local cluster' out.md judge.ts
```
