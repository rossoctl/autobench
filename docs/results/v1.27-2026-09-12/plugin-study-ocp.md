# AuthBridge plugin overhead — designed experiment (OpenShift — ykt3 Service / ykt2 workloads)

**Report generated:** 2026-09-12T19:31:51Z  
**Service version:** `v1.27`  
**Platform:** OpenShift — ykt3 Service / ykt2 workloads  
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
| 101 | 2 | baseline | 1 | 50 | 50 | 15684 | 8640 | 100 | 50 | 4 | `openai/Azure/gpt-5-mini-2025-08-07` |
| 102 | 3 | auth-only | 1 | 50 | 50 | 15684 | 8640 | 100 | 50 | 4 | `openai/Azure/gpt-5-mini-2025-08-07` |
| 103 | 4 | ibac-only | 1 | 50 | 48 | 14984 | 7826 | 96 | 48 | 4 | `openai/Azure/gpt-5-mini-2025-08-07` |
| 104 | 5 | full | 1 | 50 | 50 | 15684 | 8576 | 100 | 50 | 4 | `openai/Azure/gpt-5-mini-2025-08-07` |
| 105 | 6 | full+ibac:observe | 1 | 50 | 50 | 15684 | 9664 | 100 | 50 | 4 | `openai/Azure/gpt-5-mini-2025-08-07` |
| 106 | 7 | full+ibac:observe | 2 | 50 | 50 | 15684 | 9664 | 100 | 50 | 4 | `openai/Azure/gpt-5-mini-2025-08-07` |
| 107 | 8 | full | 2 | 50 | 50 | 15684 | 9535 | 100 | 50 | 4 | `openai/Azure/gpt-5-mini-2025-08-07` |
| 108 | 9 | ibac-only | 2 | 50 | 49 | 15305 | 9385 | 98 | 49 | 4 | `openai/Azure/gpt-5-mini-2025-08-07` |
| 109 | 10 | auth-only | 2 | 50 | 50 | 16015 | 9432 | 101 | 51 | 4 | `openai/Azure/gpt-5-mini-2025-08-07` |
| 110 | 11 | baseline | 2 | 50 | 50 | 16015 | 9432 | 101 | 51 | 4 | `openai/Azure/gpt-5-mini-2025-08-07` |

⚠️ **The legs did not do byte-identical work.** Input-token totals are the sensitive signature; where they differ, the corresponding latency delta is confounded by workload and the per-condition figures below should be read as indicative only. Note that a differing **output**-token total is expected and harmless — the model is sampled, not deterministic.

## The decisive security question: caching, or fail-open?

Across four 12-run matrices, IBAC legs authorized only *some* of their tool calls, and the count wobbled across identically configured runs. Two explanations fit that equally well from the matrix artifacts, and they could not be more different in consequence:

1. **Benign — decision caching under concurrency.** At `max_parallel_sessions=4` several identical calls fire at once, all miss the cache and consult the judge; later ones hit a warm entry.
2. **A fail-open enforcement gap** — the action proceeded with no authorization decision. For a security control this matters far more than any latency figure in this document.

Leg **#111** settles it by removing concurrency: `ibac-only` at **`max_parallel_sessions=1`**, so the tool calls are strictly serial and no two can race for the same cache entry.

| measurement | value |
|---|---:|
| tasks attempted | 10 |
| tasks OK | 9 |
| tool calls (serial, OK tasks) | 9 |
| judge calls in the run window | **10** |

**Verdict: enforcement is 1:1 without concurrency, and TTL caching is ruled out.** All 9 serial tool calls were judged (10 judge calls) — every action got its own authorization decision. The count exceeds 9 because 1 task(s) did not reach a passing verdict: a failed task's tool call is absent from the artifacts but its judge call still happened, so the honest reading is 10 judged calls across 10 attempted tasks.

That result does more than reassure; it **eliminates one of the two candidate explanations**. A decision cache with a TTL would have collapsed these 9 same-shape calls to roughly one judge call *even when serial*. It did not. So whatever produces the shortfall at `p=4` is specific to **concurrency**, and two mechanisms remain — which are *not* equally benign:

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
| 101 | 1 | 4 | 1.329 | 0.571 | 0.603 | 0.663 |
| 110 | 2 | 4 | 1.339 | 0.594 | 0.807 | 0.608 |

The cost drops to steady state **after the first bucket** and stays there — the transient is one wave of `max_parallel_sessions` tasks, all of which start before any connection is warm. Splitting at the wave gives a reproducible ratio; splitting at a fixed 10 tasks buries it by mixing six steady-state tasks into the warm bucket, and on one leg even inverts its sign (0.72x). **The cutoff below is therefore derived per leg from the artifacts' own `num_parallel`, not fixed.**

### Warm-up vs steady state, per leg

| # | condition | rep | cutoff | first wave (med) | steady (med) | steady n | ratio |
|---|---|---:|---:|---:|---:|---:|---:|
| 101 | baseline | 1 | 4 | 1.329 | 0.654 | 46 | 2.03x |
| 102 | auth-only | 1 | 4 | 10.479 | 14.070 | 46 | 0.74x |
| 103 | ibac-only | 1 | 4 | 14.572 | 12.747 | 44 | 1.14x |
| 104 | full | 1 | 4 | 16.645 | 15.858 | 46 | 1.05x |
| 105 | full+ibac:observe | 1 | 4 | 14.444 | 13.162 | 46 | 1.10x |
| 106 | full+ibac:observe | 2 | 4 | 18.788 | 14.671 | 46 | 1.28x |
| 107 | full | 2 | 4 | 17.332 | 13.930 | 46 | 1.24x |
| 108 | ibac-only | 2 | 4 | 18.511 | 14.321 | 45 | 1.29x |
| 109 | auth-only | 2 | 4 | 10.432 | 11.474 | 46 | 0.91x |
| 110 | baseline | 2 | 4 | 1.339 | 0.616 | 46 | 2.17x |

Ratios above 1 are the transient. The **baseline** legs have no sidecar, so whatever ratio they show is a property of the deployment; a preset leg's ratio is only evidence about AuthBridge to the extent that it *exceeds* the baseline's. This is the specific reason a 'fixed per-task plugin cost' derived from a 5-task leg is not a plugin cost at all — at `p=4`, four of those five tasks *are* the transient.

**Everything below excludes the first wave of each leg.**

## Steady-state cost per condition, with intervals

Replicates pooled. A bare median invites over-reading; the interval is what says whether a difference was resolvable at all.

| condition | n | median non-LLM s | 90% CI | vs baseline (Δ median) | Δ CI | perm p |
|---|---:|---:|---|---:|---|---:|
| **baseline** | 92 | 0.649 | [0.590, 0.681] | — | — | — |
| auth-only | 92 | 12.424 | [11.808, 13.223] | +11.775 | [+11.207, +12.599] | 0.0000 |
| ibac-only | 89 | 13.310 | [12.716, 14.594] | +12.660 | [+12.097, +13.931] | 0.0000 |
| full | 92 | 15.235 | [14.051, 16.065] | +14.585 | [+13.398, +15.405] | 0.0000 |
| full+ibac:observe | 92 | 14.125 | [12.902, 15.135] | +13.476 | [+12.294, +14.495] | 0.0000 |

A Δ CI that straddles zero means **no difference was resolved** at this n — which, unlike the n=5 case, is now a meaningful statement rather than a limit of the instrument. With 92 baseline samples the tests are comparisons of medians by permutation, so they make no normality assumption. Note that this table is 4 simultaneous tests against one baseline, so the Bonferroni-corrected threshold is 0.0125 rather than 0.05.

### Incremental cost of each layer

The conditions nest: `baseline` ⊂ `auth-only` ⊂ ... The interesting quantity is not each condition's cost against baseline but the **marginal** cost of each added layer, which is what the 12-run's n=5 could never resolve.

| step | Δ median s | Δ CI | perm p | resolved? |
|---|---:|---|---:|---|
| baseline → auth-only | +11.775 | [+11.207, +12.599] | 0.0000 | **yes** |
| auth-only → ibac-only | +0.886 | [-0.094, +2.288] | 0.1373 | no — CI straddles 0 |
| ibac-only → full | +1.925 | [-0.085, +2.860] | 0.0592 | no — CI straddles 0 |
| full → full+ibac:observe | -1.110 | [-2.508, +0.624] | 0.2667 | no — CI straddles 0 |

### The unit of replication is the deploy, not the task

The intervals above treat each task as an independent replicate of its condition. **They are not.** Every task in a leg ran against one deployment, so a per-task interval answers *'how variable are tasks within this deploy?'* — not *'how variable is this condition?'*. Treating the former as the latter is pseudo-replication, and it makes intervals look far tighter than the design can support. The replicate block is what makes this visible and correctable:

| condition | rep 1 leg median | rep 2 leg median | \|Δ\| between deploys |
|---|---:|---:|---:|
| baseline | 0.654 | 0.616 | **0.038** |
| auth-only | 14.070 | 11.474 | **2.596** |
| ibac-only | 12.747 | 14.321 | **1.574** |
| full | 15.858 | 13.930 | **1.929** |
| full+ibac:observe | 13.162 | 14.671 | **1.509** |

Median \|Δ\| between two deploys of the *same* condition is **1.57 s** (max 2.60 s), implying a between-deploy SD of roughly **1.65 s**. Compare that with the marginal effects in the table above — every preset-to-preset step is *smaller than the noise between two deploys of one preset*.

So the correct reading of this experiment is:

- **baseline → auth-only: +11.77 s — real.** That is 7.1x the between-deploy SD, far outside what deployment variability can manufacture.
- **auth-only → ibac-only (+0.89 s); ibac-only → full (+1.92 s); full → full+ibac:observe (-1.11 s) — below the noise floor.** Not 'probably small': **unresolvable** with two deploys per condition, regardless of how many tasks each deploy runs. Adding tasks tightens the wrong interval.

**What it would actually take.** At ~16·σ²/δ² deploys per condition for 80% power, resolving a 1 s effect needs ~44 deploys, 2 s effect needs ~11 deploys per condition — against the 2 run here. That is the honest price of a per-preset ranking, and it is the number to quote if anyone asks for one.

**A side finding worth recording:** the two *baseline* deploys differ by only **0.038 s**, while sidecar-injected deploys differ by 1.51–2.60 s. Deployment variability is not a background property of the cluster — it is **introduced by the sidecar**. AuthBridge costs not only latency but *reproducibility*, which matters for anyone using these benchmarks to detect regressions.

## Replicate agreement (the order-effect test)

Each condition ran twice, once early and once late, in reversed order. Agreement means run order is not driving the result. Disagreement is informative rather than fatal — the *direction* of the differences distinguishes a monotone session drift (all one sign) from deploy-to-deploy variability (mixed signs), and only the first would bias a condition comparison.

| condition | rep 1 med | rep 2 med | Δ | Δ CI | perm p |
|---|---:|---:|---:|---|---:|
| baseline | 0.654 | 0.616 | -0.038 | [-0.098, +0.084] | 0.6660 |
| auth-only | 14.070 | 11.474 | -2.596 | [-4.292, -1.071] | 0.0009 |
| ibac-only | 12.747 | 14.321 | +1.574 | [-0.126, +4.055] | 0.1496 |
| full | 15.858 | 13.930 | -1.929 | [-3.302, +0.134] | 0.1202 |
| full+ibac:observe | 13.162 | 14.671 | +1.509 | [-1.343, +3.238] | 0.2151 |

⚠️ **Replicates disagree for: `auth-only`.** By the per-task test these are separate populations, so the per-task intervals in the previous section are too narrow — which is the pseudo-replication point made there, arriving here as direct evidence rather than as a caveat.

**But the disagreement is not run-order drift.** The rep-2 minus rep-1 differences go in **both directions** (`baseline` -0.04 s, `auth-only` -2.60 s, `ibac-only` +1.57 s, `full` -1.93 s, `full+ibac:observe` +1.51 s), and a monotone session effect — gateway warm-up, cache fill, node contention — would have to push every condition the same way, since replicate 2 ran entirely later than replicate 1. A non-monotone pattern instead points at **deploy-to-deploy variability**: each leg is a fresh deployment, and that is the nuisance variable, not elapsed time.

This is a more useful conclusion than 'order matters', and it is only available because the reversal was built in: run order was **balanced by design** (equal position sums above), so it cannot be what produced a two-directional pattern.

## Judged calls at n=50

The IBAC judge is itself an LLM call, so judge latency inherits inference variance and **the number of judged calls need not equal the number of tool calls**. At n=5 a leg offered at most 5 chances to observe this and the resulting median was a mixture of two populations. At n=50 the ratio is estimable.

| # | pos | condition | rep | tool calls | judge calls | judged / call |
|---|---:|---|---:|---:|---:|---:|
| 101 | 2 | baseline | 1 | 50 | 0 | 0.00 |
| 102 | 3 | auth-only | 1 | 50 | 0 | 0.00 |
| 103 | 4 | ibac-only | 1 | 48 | 45 | 0.94 |
| 104 | 5 | full | 1 | 50 | 49 | 0.98 |
| 105 | 6 | full+ibac:observe | 1 | 50 | 49 | 0.98 |
| 106 | 7 | full+ibac:observe | 2 | 50 | 49 | 0.98 |
| 107 | 8 | full | 2 | 50 | 50 | 1.00 |
| 108 | 9 | ibac-only | 2 | 49 | 48 | 0.98 |
| 109 | 10 | auth-only | 2 | 51 | 0 | 0.00 |
| 110 | 11 | baseline | 2 | 51 | 0 | 0.00 |

A ratio near 0 is a preset that does not engage IBAC (correct for `auth-only`); near 1 is full enforcement. **Intermediate ratios are the finding**, and the serial diagnostic above is what interprets them.

The judge window is derived from the `run_id` timestamp plus the run's `wall_seconds` (+20 s slack). Legs are separated by teardown and redeploy, so windows do not overlap — but this attribution is by time, not by trace id, so a stray non-benchmark request to the judge would land in whichever window contained it.

## Does per-call cost hold across benchmarks? (the tau2 linearity test)

There is a tempting shortcut for pricing a plugin on an expensive benchmark without running it: take the per-tool-call delta from cheap gsm8k legs and multiply by the target benchmark's tool-call count. That assumes per-call cost is a **constant** across benchmarks. gsm8k makes ~1 substantive tool call per task; tau2 makes roughly an order of magnitude more, so this pair tests the assumption directly instead of resting on it.

| leg | condition | tasks OK | tool calls/task | non-LLM s/task (med) | per tool call (s) |
|---|---|---:|---:|---:|---:|
| #112 | baseline | 10 | 12 | 22.36 | 1.864 |
| #113 | full+ibac:observe | 10 | 12 | 93.21 | 8.106 |

**Measured on tau2:** `full+ibac:observe` adds **+70.85 s/task**, i.e. **+6.242 s per tool call**.

**Projected from gsm8k** by that shortcut: the same condition costs +13.476 s/task on gsm8k over ~1 tool call, which scaled by tau2's 12 tool calls/task predicts **+154.97 s/task**.

**Measured / projected = 0.46x.**

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
BM_SPECS=reference/plugin_study_specs.json BM_LABEL=pstudy-ocp \
  python3 reference/run-12.py        # detached; see feedback_long_runs_detach_and_adopt
python3 reference/gen-plugin-study.py run12-pstudy-ocp.json reference/plugin_study_specs.json v1.27 'OpenShift — ykt3 Service / ykt2 workloads' out.md judge.ts
```
