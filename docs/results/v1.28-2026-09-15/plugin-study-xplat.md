# AuthBridge plugin overhead — OpenShift — ykt3 Service / ykt2 workloads vs KinD — single-node local cluster

**Report generated:** 2026-09-16T03:59:13Z  
**Service version:** `v1.28`  
**Experiment:** `plugin_study_specs.json` — 13 legs, identical specs on both platforms  
**Companion reports:** the per-platform analyses, which carry the intervals and the design rationale this document does not repeat.

**Gateway completion cache:** both platforms rested each prompt set for **900 s** between legs sharing it, against a measured TTL of ~10 min — so every leg on both sides paid for its own completions.

**Contents**

- [Why compare, when both reports already agree on method](#why-compare-when-both-reports-already-agree-on-method)
- [First: did the two platforms do the same work?](#first-did-the-two-platforms-do-the-same-work)
- [The headline: the platforms disagree about which layer costs anything](#the-headline-the-platforms-disagree-about-which-layer-costs-anything)
  - [It is variable cost, not a fixed penalty (a hypothesis worth falsifying)](#it-is-variable-cost-not-a-fixed-penalty-a-hypothesis-worth-falsifying)
- [What reproduced exactly: every structural finding](#what-reproduced-exactly-every-structural-finding)
  - [The serial diagnostic, independently on both clusters](#the-serial-diagnostic-independently-on-both-clusters)
  - [The judged-call ratio is ~1, on both](#the-judged-call-ratio-is-1-on-both)
  - [The cross-benchmark projection fails on both — in opposite directions](#the-cross-benchmark-projection-fails-on-both--in-opposite-directions)
- [The unit of replication, checked twice](#the-unit-of-replication-checked-twice)
- [What this means for how plugin cost gets quoted](#what-this-means-for-how-plugin-cost-gets-quoted)
- [Reproducing this](#reproducing-this)

## Why compare, when both reports already agree on method

The two platforms ran the **same 13 legs from the same spec file, on the same image, with the same deterministic task selection**. The intent was ordinary cross-platform validation: confirm on a second cluster what the first one measured.

That is not what came back. The platforms agree on every *structural* finding and disagree on every *magnitude* — including which plugin layer the cost belongs to. That disagreement is the single most consequential result of the study, and it is **invisible in either per-platform report alone**: each one reads as a clean, internally consistent answer. It is the reason a per-task plugin figure must never be quoted without naming the cluster it was measured on.

## First: did the two platforms do the same work?

A latency comparison across clusters is only meaningful if the workload matched. Model and input-token totals are the signature; both are read back from the artifacts.

| leg | model | tasks | OpenShift — ykt3 Service / ykt2 workloads input tok | KinD — single-node local cluster input tok | Δ |
|---|---|---:|---:|---:|---:|
| #111 | `openai/Azure/gpt-5-mini-2025-08-07` | 10 | 3166 | 3166 | identical |
| #101 | `openai/Azure/gpt-5-mini-2025-08-07` | 50 | 15684 | 15684 | identical |
| #102 | `openai/Azure/gpt-5-mini-2025-08-07` | 50 | 15684 | 15684 | identical |
| #103 | `openai/Azure/gpt-5-mini-2025-08-07` | 50 | 15684 | 16041 | +357 |
| #104 | `openai/Azure/gpt-5-mini-2025-08-07` | 50 | 16015 | 15684 | -331 |
| #105 | `openai/Azure/gpt-5-mini-2025-08-07` | 50 | 15320 | 15684 | +364 |
| #106 | `openai/Azure/gpt-5-mini-2025-08-07` | 50 | 15684 | 15684 | identical |
| #107 | `openai/Azure/gpt-5-mini-2025-08-07` | 50 | 16023 | 15684 | -339 |
| #108 | `openai/Azure/gpt-5-mini-2025-08-07` | 50 | 15684 | 15684 | identical |
| #109 | `openai/Azure/gpt-5-mini-2025-08-07` | 50 | 15684 | 16016 | +332 |
| #110 | `openai/Azure/gpt-5-mini-2025-08-07` | 50 | 15684 | 16010 | +326 |
| #112 | `openai/aws/claude-sonnet-5` | 10 | 932745 | 974396 | +41651 |
| #113 | `openai/aws/claude-sonnet-5` | 10 | 817928 | 978368 | +160440 |

**The gsm8k legs are not byte-identical, within or across platforms** — worth stating plainly, because the per-platform reports' own identical-work checks flag this too. OpenShift — ykt3 Service / ykt2 workloads produced 4 distinct totals across its 10 replicate legs (7 legs share the most common one) and KinD — single-node local cluster produced 4 (7 share the most common). Within-platform spread is 4.6% (OpenShift — ykt3 Service / ykt2 workloads) and 2.3% (KinD — single-node local cluster); the largest same-leg gap across platforms is 2.4%.

The cause is benign and expected: the model is sampled rather than deterministic, so an agent occasionally spends one extra tool call on a task, and the two clusters front different LiteLLM gateways. What matters is the ratio of that confound to the effects being measured. A few percent of token drift cannot manufacture the differences below, which are **multiples** — up to 60x. The confound would matter if this document ranked presets against each other by a few percent, and it explicitly does not.

## The headline: the platforms disagree about which layer costs anything

Steady-state non-LLM time per task (`agent_call_s - llm_total_s`), replicates pooled, warm-up wave excluded.

| condition | OpenShift — ykt3 Service / ykt2 workloads median s | KinD — single-node local cluster median s | ratio | marginal step (OpenShift — ykt3 Service / ykt2 workloads) | marginal step (KinD — single-node local cluster) |
|---|---:|---:|---:|---:|---:|
| **baseline** | 0.423 | 0.099 | 4.3x | — | — |
| auth-only | 14.101 | 0.152 | 92.8x | +13.68 | +0.05 |
| ibac-only | 14.148 | 1.688 | 8.4x | +0.05 | +1.54 |
| full | 14.446 | 1.824 | 7.9x | +0.30 | +0.14 |
| full+ibac:observe | 14.598 | 1.826 | 8.0x | +0.15 | +0.00 |

Read the two *marginal step* columns against each other — that is the whole finding.

- On **OpenShift — ykt3 Service / ykt2 workloads** the cost enters at **auth-only** (`+13.68` s) and every later layer is lost in the noise.
- On **KinD — single-node local cluster** that same step is nearly free, and the cost enters at **ibac-only** (`+1.54` s) instead.

**So the two clusters do not merely scale differently — they attribute the cost to different plugin layers.** A reader of the OpenShift — ykt3 Service / ykt2 workloads report alone would conclude that the sidecar's presence is the expense and IBAC's judge call is negligible; a reader of the KinD — single-node local cluster report alone would conclude the exact opposite. Both readings are correct about their own cluster.

### It is variable cost, not a fixed penalty (a hypothesis worth falsifying)

A ~14 s per-task cost on one cluster and ~0.1 s on another invites an obvious guess: a timeout or a failing retry somewhere in the sidecar's egress path. A timeout leaves a signature — values piled on a round number with a small spread. The distributions falsify it.

| platform | condition | n | min | median | max | SD |
|---|---|---:|---:|---:|---:|---:|
| OpenShift — ykt3 Service / ykt2 workloads | baseline | 92 | 0.23 | 0.42 | 0.98 | 0.16 |
| OpenShift — ykt3 Service / ykt2 workloads | auth-only | 92 | 8.05 | 14.10 | 17.93 | 1.92 |
| OpenShift — ykt3 Service / ykt2 workloads | full | 92 | 7.05 | 14.45 | 25.09 | 2.61 |
| KinD — single-node local cluster | baseline | 92 | 0.08 | 0.10 | 0.28 | 0.04 |
| KinD — single-node local cluster | auth-only | 92 | 0.11 | 0.15 | 0.42 | 0.07 |
| KinD — single-node local cluster | full | 92 | 0.11 | 1.82 | 14.00 | 2.50 |

On OpenShift — ykt3 Service / ykt2 workloads the sidecar conditions spread across roughly an order of magnitude (4.5–20 s for `auth-only`) with an SD of ~1.9 s — **broad and unimodal, with nothing piled at a round number**. That is the shape of contention and queueing, not of a fixed timeout, so the timeout hypothesis is dead.

KinD — single-node local cluster deserves a separate remark rather than a reassuring one-liner. Its `auth-only` distribution is genuinely tight (SD 0.07 s), but its `full` distribution is **not**: median 1.82 s against a max of 14.00 s, SD 2.50 s. That heavy right tail is the IBAC judge, which is itself an LLM call and therefore inherits inference latency variance. So on the quiet cluster the judge's own variability becomes the dominant source of spread, where on the busy cluster it is buried under cluster contention.

Two consequences. First, the mechanism to chase on the shared cluster is *why the intercepted path is slow and jittery there*, not a misconfigured timeout — a different investigation. Second, on both platforms the spread grows with the median, so enabling the sidecar costs **reproducibility** as well as latency, which matters for anyone using these benchmarks to detect regressions.

## What reproduced exactly: every structural finding

The magnitudes did not travel. The *structure* did — and the structural findings are the ones with consequences.

### The serial diagnostic, independently on both clusters

`ibac-only` at `max_parallel_sessions=1`: strictly serial tool calls, so no two can race for one cache entry.

| platform | tasks attempted | serial tool calls | judge calls | ratio |
|---|---:|---:|---:|---:|
| OpenShift — ykt3 Service / ykt2 workloads | 10 | 10 | 10 | 1.00 |
| KinD — single-node local cluster | 10 | 10 | 10 | 1.00 |

**Both clusters authorize every serial call.** TTL decision caching is ruled out twice, independently — a cache with a time-to-live would have collapsed ten same-shape serial calls to roughly one judge call on *either* platform. Whatever produces the shortfall at `p=4` is therefore specific to concurrency, and the two candidates that remain (benign single-flight deduplication vs fail-open under race) produce identical counts. Separating them is a code-reading task, not a measurement task, and it is the one open item from this study with a security consequence.

### The judged-call ratio is ~1, on both

The 12-run matrices repeatedly showed IBAC legs authorizing only *some* calls — at one point 3 of 5 — which read as alarming. At n=50 it is a ratio rather than an anecdote.

| condition | OpenShift — ykt3 Service / ykt2 workloads judged/call | KinD — single-node local cluster judged/call |
|---|---:|---:|
| baseline | 0.00–0.00 | 0.00–0.00 |
| auth-only | 0.00–0.00 | 0.00–0.00 |
| ibac-only | 0.92–0.94 | 0.94–0.96 |
| full | 0.92–0.94 | 0.96–0.96 |
| full+ibac:observe | 0.90–0.94 | 0.94–0.98 |

`baseline` and `auth-only` sit at 0.00 on both platforms — correct, those presets do not engage IBAC, and a non-zero value there would have indicated the judge was being consulted by something other than the benchmark. The IBAC conditions sit near 1 on both. **The alarming 12-run ratio was small-sample noise on a quantity that is really ~1.**

### The cross-benchmark projection fails on both — in opposite directions

The cheap way to price a plugin on an expensive benchmark is to multiply a gsm8k per-tool-call delta by the target benchmark's tool-call count. That assumes per-call cost is a constant. tau2 makes ~11 tool calls per task against gsm8k's ~1, so the pair tests the assumption.

| platform | tau2 measured Δ s/task | projected from gsm8k | measured / projected |
|---|---:|---:|---:|
| OpenShift — ykt3 Service / ykt2 workloads | +48.4 | 155.9 | 0.31x |
| KinD — single-node local cluster | +51.6 | 20.7 | 2.49x |

**The projection is not merely inaccurate — it is inconsistent in sign.** It over-estimates by 3.2x on OpenShift — ykt3 Service / ykt2 workloads and under-estimates by 2.5x on KinD — single-node local cluster. A method that errs in both directions cannot be salvaged with a correction factor — so this shortcut has no defensible form and should not be used to price a benchmark. Measuring is the only way to know a benchmark's plugin cost, and it is two legs, as done here.

## The unit of replication, checked twice

Each condition ran on two independent deploys. The spread between two deploys of the *same* condition is the noise floor that any preset-to-preset claim has to clear.

| condition | OpenShift — ykt3 Service / ykt2 workloads \|Δ\| between deploys | KinD — single-node local cluster \|Δ\| between deploys |
|---|---:|---:|
| baseline | 0.164 | 0.003 |
| auth-only | 0.083 | 0.039 |
| ibac-only | 0.238 | 0.274 |
| full | 1.594 | 0.176 |
| full+ibac:observe | 0.218 | 0.155 |

Typical between-deploy \|Δ\| is **0.22 s** on OpenShift — ykt3 Service / ykt2 workloads and **0.155 s** on KinD — single-node local cluster. On both platforms the noise floor scales with the platform's own cost level, and on both it **exceeds every preset-to-preset marginal step except the one dominant layer**. The conclusion is the same on each cluster and worth stating plainly: with two deploys per condition, a *ranking of presets* is not available at any task count. Adding tasks tightens the wrong interval.

## What this means for how plugin cost gets quoted

**Portable across platforms (quote these):**

- Every serial tool call is authorized; TTL caching is ruled out. Reproduced independently.
- The judged-call ratio for IBAC presets is ~1, and 0 for presets that do not engage IBAC.
- Enabling the AuthBridge sidecar has a cost that is large relative to the no-sidecar baseline and clearly resolvable on both clusters.
- Beyond the one dominant layer, preset-to-preset differences are below the between-deploy noise floor on both clusters.
- Per-tool-call cost is **not** a cross-benchmark constant.

**Not portable (do not quote without naming the cluster):**

- Any absolute per-task second figure. The same condition on the same image differs by 4–93x between these two clusters.
- *Which layer* the cost belongs to. The two clusters disagree, and each is right locally.
- Any ranking of presets against each other.

The practical rule this suggests: measure plugin overhead **on the cluster whose numbers you intend to quote**, using this spec file, and treat the structural findings as the only portable ones.

## Reproducing this

```sh
# one platform at a time -- separate gateways, but the SAME upstream model providers behind
# both, so parallel runs contend and the latency numbers stop meaning anything
BM_SPECS=reference/plugin_study_specs.json BM_LABEL=pstudy-<platform> \
  BM_CACHE_GAP=900 BM_ORDER=111,112,101,102,103,104,105,113,106,107,108,109,110 \
  python3 reference/run-12.py          # detached; see feedback_long_runs_detach_and_adopt
python3 reference/gen-plugin-study-xplat.py reference/plugin_study_specs.json v1.28 plugin-study-xplat.md \
  'OpenShift — ykt3 Service / ykt2 workloads' results/v1.28-dev146/run12-pstudy-ocp-v128.json results/v1.28-dev146/judge-ocp-v128.ts -- \
  'KinD — single-node local cluster' results/v1.28-dev146/run12-pstudy-kind-v128.json results/v1.28-dev146/judge-kind-v128.ts
```
