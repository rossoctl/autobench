# AuthBridge plugin overhead — designed experiment (KinD — single-node local)

**Report generated:** 2026-10-05T23:42:32Z  
**Service version:** `v1.35`  
**Platform:** KinD — single-node local  
**Legs executed:** 13 of 13  
**Judge-call evidence:** included  
**Statistics:** 90% bootstrap CIs (10,000 resamples), permutation tests (20,000 shuffles), seed `20260912`

**Contents**

- [What this measures, and why it needed its own runs](#what-this-measures-and-why-it-needed-its-own-runs)
- [Run order was balanced by design (the crossover)](#run-order-was-balanced-by-design-the-crossover)
- [The comparison is like-for-like (identical work)](#the-comparison-is-like-for-like-identical-work)
- [The gateway's completion cache is spaced out (the other precondition)](#the-gateways-completion-cache-is-spaced-out-the-other-precondition)
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
| each preset runs **once at `max_tasks=5`** | resampling *this run's own* steady-state baseline tasks down to n=5 puts the minimum detectable preset-to-preset difference at **1.5x–1.8x**; at n=50 the same calculation gives **6–28%**. The pairs of interest differ by far less than the n=5 figure, so a null result there is *underpowered*, not reassuring | `max_tasks=50` |
| per-task figures are dominated by **warm-up** | in 50-task baseline legs, which have **no sidecar at all**, the first concurrency wave costs **5.51x, 10.65x** steady state and the transient is gone by the second wave; at `p=4` that is *four of the five tasks* an n=5 leg measures | excluding the first wave, and reporting it **separately** |
| **run order** aliases onto the plugin variable | the legs run in one fixed sequence, so any monotone drift loads onto whichever preset ran last | a **reversed-order replicate** |

One property of the 12-run design is worth keeping, and this experiment inherits it: **task selection is deterministic**, so every leg runs the same tasks in the same order with the same model. That is verified below before any latency figure is quoted.

## Run order was balanced by design (the crossover)

Replicate 2 runs the five conditions in **reverse** order. That makes each condition's mean run position identical, so a smooth drift over the session — gateway warm-up, cache fill, node contention — cancels between replicates instead of being attributed to a preset.

| condition | rep 1 position | rep 2 position | position sum |
|---|---:|---:|---:|
| baseline | 3 | 13 | 16 |
| auth-only | 4 | 12 | 16 |
| ibac-only | 5 | 11 | 16 |
| full | 6 | 10 | 16 |
| full+ibac:observe | 7 | 9 | 16 |

**Balanced** — every condition has the same position sum, so run order cannot favour any one of them.

## The comparison is like-for-like (identical work)

If every leg did the same work, a latency difference is attributable to the plugin configuration. `num_parallel` is read back from the artifacts rather than from the request, so a leg that silently ran at the wrong concurrency cannot pass unnoticed.

| # | pos | condition | rep | tasks | OK | input tok | output tok | LLM calls | tool calls | p | model |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 101 | 3 | baseline | 1 | 50 | 50 | 15684 | 9358 | 50 | 50 | 4 | `openai/Azure/gpt-5-mini-2025-08-07` |
| 102 | 4 | auth-only | 1 | 50 | 50 | 15684 | 8526 | 50 | 50 | 4 | `openai/Azure/gpt-5-mini-2025-08-07` |
| 103 | 5 | ibac-only | 1 | 50 | 49 | 15684 | 7502 | 50 | 50 | 4 | `openai/Azure/gpt-5-mini-2025-08-07` |
| 104 | 6 | full | 1 | 50 | 50 | 15684 | 9614 | 50 | 50 | 4 | `openai/Azure/gpt-5-mini-2025-08-07` |
| 105 | 7 | full+ibac:observe | 1 | 50 | 50 | 15684 | 9294 | 50 | 50 | 4 | `openai/Azure/gpt-5-mini-2025-08-07` |
| 106 | 9 | full+ibac:observe | 2 | 50 | 50 | 15684 | 8398 | 50 | 50 | 4 | `openai/Azure/gpt-5-mini-2025-08-07` |
| 107 | 10 | full | 2 | 50 | 50 | 15684 | 9166 | 51 | 50 | 4 | `openai/Azure/gpt-5-mini-2025-08-07` |
| 108 | 11 | ibac-only | 2 | 50 | 47 | 16034 | 8744 | 52 | 51 | 4 | `openai/Azure/gpt-5-mini-2025-08-07` |
| 109 | 12 | auth-only | 2 | 50 | 50 | 15684 | 10574 | 50 | 50 | 4 | `openai/Azure/gpt-5-mini-2025-08-07` |
| 110 | 13 | baseline | 2 | 50 | 50 | 15684 | 7438 | 50 | 50 | 4 | `openai/Azure/gpt-5-mini-2025-08-07` |

⚠️ **The legs did not do byte-identical work.** Input-token totals are the sensitive signature; where they differ, the corresponding latency delta is confounded by workload and the per-condition figures below should be read as indicative only. Note that a differing **output**-token total is expected and harmless — the model is sampled, not deterministic.

## The gateway's completion cache is spaced out (the other precondition)

Every gsm8k leg in this study sends the **same 50 prompts to the same model** — that is what makes the conditions comparable — and the LLM gateway caches completions keyed on the request body for a **measured TTL of ~10 minutes**. Two legs run back to back therefore do not both pay for their completions: the second is served the first's, `usage` and all. For a study whose outcome *is* latency this is not noise, it is the measurement disappearing, and it disappears **in run order**, which is indistinguishable from a plugin effect by shape.

The tell needs no statistics, and it is checked here rather than argued: the conditions are **nested** — every non-baseline condition is baseline *plus* the sidecar — so no leg carrying the sidecar can be faster than a leg without one. Replay contamination breaks that ordering outright, because a leg served from cache skips work the leg it nests above actually did. On this run the fastest `baseline` leg's steady median is **0.093 s** and the fastest sidecar-carrying leg's is **0.124 s** (1.3x above it): **no leg violates the ordering.** Still compare the wall times in nesting order before believing any of them, and remember the ibac judge is itself a call through the same gateway.

**✅ This execution was spaced.** The driver rested each prompt set for at least **900 s** before reusing it — against the ~10 min TTL, a 1.5x margin — across 2 prompt group(s) (`gsm8k:default`, `tau2:openai/aws/claude-sonnet-5`), sleeping out the remainder on 8 of 13 legs. The gap is measured from the previous leg's *finish*, which errs safe: a shared task set is always a prefix, so the colliding prompts were sent near that leg's start and are older still. **Every leg below paid for its own completions**, so the latency it reports is the latency of doing the work.

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
| 101 | 1 | 4 | 0.511 | 0.092 | 0.088 | 0.093 |
| 110 | 2 | 4 | 1.130 | 0.099 | 0.090 | 0.122 |

The cost drops to steady state **after the first bucket** and stays there — the transient is one wave of `max_parallel_sessions` tasks, all of which start before any connection is warm. The choice of cutoff is not cosmetic. On the baseline legs above, splitting at the wave gives 5.51x, 10.65x; splitting at a fixed 10 tasks gives 1.17x, 0.88x — the fixed cutoff buries the effect by mixing steady-state tasks into the 'warm' bucket, and can drive the ratio below 1, reporting warm-up as a *speed-up*. **The cutoff below is therefore derived per leg from the artifacts' own `num_parallel`, not fixed.**

### Warm-up vs steady state, per leg

| # | condition | rep | cutoff | first wave (med) | steady (med) | steady n | ratio |
|---|---|---:|---:|---:|---:|---:|---:|
| 101 | baseline | 1 | 4 | 0.511 | 0.093 | 46 | 5.51x |
| 102 | auth-only | 1 | 4 | 0.538 | 0.136 | 46 | 3.95x |
| 103 | ibac-only | 1 | 4 | 2.629 | 1.701 | 46 | 1.55x |
| 104 | full | 1 | 4 | 5.118 | 1.577 | 46 | 3.25x |
| 105 | full+ibac:observe | 1 | 4 | 1.954 | 1.672 | 46 | 1.17x |
| 106 | full+ibac:observe | 2 | 4 | 2.528 | 1.528 | 46 | 1.65x |
| 107 | full | 2 | 4 | 3.689 | 1.702 | 46 | 2.17x |
| 108 | ibac-only | 2 | 4 | 1.860 | 1.516 | 45 | 1.23x |
| 109 | auth-only | 2 | 4 | 0.760 | 0.124 | 46 | 6.14x |
| 110 | baseline | 2 | 4 | 1.130 | 0.106 | 46 | 10.65x |

Ratios above 1 are the transient. The **baseline** legs have no sidecar, so whatever ratio they show is a property of the deployment; a preset leg's ratio is only evidence about AuthBridge to the extent that it *exceeds* the baseline's. This is the specific reason a 'fixed per-task plugin cost' derived from a 5-task leg is not a plugin cost at all — at `p=4`, four of those five tasks *are* the transient.

**Everything below excludes the first wave of each leg.**

## Steady-state cost per condition, with intervals

Replicates pooled. A bare median invites over-reading; the interval is what says whether a difference was resolvable at all.

| condition | n | median non-LLM s | 90% CI | vs baseline (Δ median) | Δ CI | perm p |
|---|---:|---:|---|---:|---|---:|
| **baseline** | 92 | 0.097 | [0.094, 0.106] | — | — | — |
| auth-only | 92 | 0.130 | [0.125, 0.135] | +0.033 | [+0.023, +0.039] | 0.0000 |
| ibac-only | 91 | 1.610 | [1.538, 1.716] | +1.512 | [+1.443, +1.615] | 0.0000 |
| full | 92 | 1.602 | [1.534, 1.798] | +1.505 | [+1.433, +1.699] | 0.0000 |
| full+ibac:observe | 92 | 1.607 | [1.549, 1.695] | +1.509 | [+1.449, +1.594] | 0.0000 |

A Δ CI that straddles zero means **no difference was resolved** at this n — which, unlike the n=5 case, is now a meaningful statement rather than a limit of the instrument. With 92 baseline samples the tests are comparisons of medians by permutation, so they make no normality assumption. Note that this table is 4 simultaneous tests against one baseline, so the Bonferroni-corrected threshold is 0.0125 rather than 0.05.

### Incremental cost of each layer

The conditions nest: `baseline` ⊂ `auth-only` ⊂ ... The interesting quantity is not each condition's cost against baseline but the **marginal** cost of each added layer, which is what the 12-run's n=5 could never resolve.

| step | Δ median s | Δ CI | perm p | resolved? |
|---|---:|---|---:|---|
| baseline → auth-only | +0.033 | [+0.023, +0.039] | 0.0000 | **yes** |
| auth-only → ibac-only | +1.480 | [+1.412, +1.584] | 0.0000 | **yes** |
| ibac-only → full | -0.008 | [-0.139, +0.208] | 0.9857 | no — CI straddles 0 |
| full → full+ibac:observe | +0.005 | [-0.205, +0.117] | 0.9340 | no — CI straddles 0 |

### The unit of replication is the deploy, not the task

The intervals above treat each task as an independent replicate of its condition. **They are not.** Every task in a leg ran against one deployment, so a per-task interval answers *'how variable are tasks within this deploy?'* — not *'how variable is this condition?'*. Treating the former as the latter is pseudo-replication, and it makes intervals look far tighter than the design can support. The replicate block is what makes this visible and correctable:

| condition | rep 1 leg median | rep 2 leg median | \|Δ\| between deploys |
|---|---:|---:|---:|
| baseline | 0.093 | 0.106 | **0.013** |
| auth-only | 0.136 | 0.124 | **0.012** |
| ibac-only | 1.701 | 1.516 | **0.184** |
| full | 1.577 | 1.702 | **0.124** |
| full+ibac:observe | 1.672 | 1.528 | **0.144** |

Median \|Δ\| between two deploys of the *same* condition is **0.12 s** (max 0.18 s), implying a between-deploy SD of roughly **0.13 s**. A step is resolvable only if it exceeds about three times that (**0.39 s**) — compare the marginal effects in the table above.

So the correct reading of this experiment is:

- **auth-only → ibac-only: +1.48 s — real.** That is 11.3x the between-deploy SD, far outside what deployment variability can manufacture.
- **baseline → auth-only (+0.03 s); ibac-only → full (-0.01 s); full → full+ibac:observe (+0.00 s) — below the noise floor.** Not 'probably small': **unresolvable** with two deploys per condition, regardless of how many tasks each deploy runs. Adding tasks tightens the wrong interval.

**What it would actually take.** At ~16·σ²/δ² deploys per condition for 80% power, σ=0.131 s here is small enough that the **2 deploys already run suffice** to resolve a 1 s effect. So the unresolved steps above are not under-replicated — they are genuinely smaller than 1 s. Resolving them would mean pinning down effects of ~0.1 s, which needs ~27 deploys per condition, and at that scale cluster drift over the required hours becomes the dominant error rather than deployment variability.

## Replicate agreement (the order-effect test)

Each condition ran twice, once early and once late, in reversed order. Agreement means run order is not driving the result. Disagreement is informative rather than fatal — the *direction* of the differences distinguishes a monotone session drift (all one sign) from deploy-to-deploy variability (mixed signs), and only the first would bias a condition comparison.

| condition | rep 1 med | rep 2 med | Δ | Δ CI | perm p |
|---|---:|---:|---:|---|---:|
| baseline | 0.093 | 0.106 | +0.013 | [+0.006, +0.038] | 0.0659 |
| auth-only | 0.136 | 0.124 | -0.012 | [-0.018, -0.005] | 0.0156 |
| ibac-only | 1.701 | 1.516 | -0.184 | [-0.574, -0.034] | 0.0641 |
| full | 1.577 | 1.702 | +0.124 | [-0.160, +0.386] | 0.4496 |
| full+ibac:observe | 1.672 | 1.528 | -0.144 | [-0.303, -0.038] | 0.0815 |

⚠️ **Replicates disagree for: `baseline`, `auth-only`, `ibac-only`, `full+ibac:observe`.** By the per-task test these are separate populations, so the per-task intervals in the previous section are too narrow — which is the pseudo-replication point made there, arriving here as direct evidence rather than as a caveat.

**But the disagreement is not run-order drift.** The rep-2 minus rep-1 differences go in **both directions** (`baseline` +0.01 s, `auth-only` -0.01 s, `ibac-only` -0.18 s, `full` +0.12 s, `full+ibac:observe` -0.14 s), and a monotone session effect — gateway warm-up, cache fill, node contention — would have to push every condition the same way, since replicate 2 ran entirely later than replicate 1. A non-monotone pattern instead points at **deploy-to-deploy variability**: each leg is a fresh deployment, and that is the nuisance variable, not elapsed time.

This is a more useful conclusion than 'order matters', and it is only available because the reversal was built in: run order was **balanced by design** (equal position sums above), so it cannot be what produced a two-directional pattern.

## Judged calls at n=50

The IBAC judge is itself an LLM call, so judge latency inherits inference variance and **the number of judged calls need not equal the number of tool calls**. At n=5 a leg offered at most 5 chances to observe this and the resulting median was a mixture of two populations. At n=50 the ratio is estimable.

| # | pos | condition | rep | tool calls | judge calls | judged / call |
|---|---:|---|---:|---:|---:|---:|
| 101 | 3 | baseline | 1 | 50 | 0 | 0.00 |
| 102 | 4 | auth-only | 1 | 50 | 0 | 0.00 |
| 103 | 5 | ibac-only | 1 | 49 | 49 | 1.00 |
| 104 | 6 | full | 1 | 50 | 48 | 0.96 |
| 105 | 7 | full+ibac:observe | 1 | 50 | 48 | 0.96 |
| 106 | 9 | full+ibac:observe | 2 | 50 | 49 | 0.98 |
| 107 | 10 | full | 2 | 50 | 48 | 0.96 |
| 108 | 11 | ibac-only | 2 | 48 | 49 | 1.02 |
| 109 | 12 | auth-only | 2 | 50 | 0 | 0.00 |
| 110 | 13 | baseline | 2 | 50 | 0 | 0.00 |

A ratio near 0 is a preset that does not engage IBAC (correct for `auth-only`); near 1 is full enforcement. **Intermediate ratios are the finding**, and the serial diagnostic above is what interprets them.

The judge window is derived from the `run_id` timestamp plus the run's `wall_seconds` (+20 s slack). Legs are separated by teardown and redeploy, so windows do not overlap — but this attribution is by time, not by trace id, so a stray non-benchmark request to the judge would land in whichever window contained it.

## Does per-call cost hold across benchmarks? (the tau2 linearity test)

There is a tempting shortcut for pricing a plugin on an expensive benchmark without running it: take the per-tool-call delta from cheap gsm8k legs and multiply by the target benchmark's tool-call count. That assumes per-call cost is a **constant** across benchmarks. gsm8k makes ~1 substantive tool call per task; tau2 makes roughly an order of magnitude more, so this pair tests the assumption directly instead of resting on it.

| leg | condition | tasks OK | tool calls/task | non-LLM s/task (med) | per tool call (s) |
|---|---|---:|---:|---:|---:|
| #112 | baseline | 10 | 12.0 | 12.06 | 1.005 |
| #113 | full+ibac:observe | 10 | 11.0 | 45.46 | 4.133 |

**Measured on tau2:** `full+ibac:observe` adds **+33.40 s/task**, i.e. **+3.128 s per tool call**.

**Projected from gsm8k** by that shortcut: the same condition costs +1.509 s/task on gsm8k over ~1 tool call, which scaled by tau2's 11.0 tool calls/task predicts **+16.60 s/task**.

**Measured / projected = 2.01x.**

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
BM_SPECS=reference/plugin_study_specs.json BM_LABEL=pstudy-kind-v135-r3 \
  BM_CACHE_GAP=900 BM_ORDER=111,112,101,102,103,104,105,113,106,107,108,109,110 \
  python3 reference/run-12.py        # detached; see feedback_long_runs_detach_and_adopt
python3 reference/gen-plugin-study.py run12-pstudy-kind-v135-r3.json reference/plugin_study_specs.json v1.35 'KinD — single-node local' out.md judge.ts
```
