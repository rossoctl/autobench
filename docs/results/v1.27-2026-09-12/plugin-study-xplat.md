# AuthBridge plugin overhead — OCP (ykt3/ykt2) vs KinD (single node)

**Report generated:** 2026-09-12T20:12:38Z  
**Service version:** `v1.27`  
**Experiment:** `plugin_study_specs.json` — 13 legs, identical specs on both platforms  
**Companion reports:** the per-platform analyses, which carry the intervals and the design rationale this document does not repeat.

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

| leg | model | tasks | OCP (ykt3/ykt2) input tok | KinD (single node) input tok | Δ |
|---|---|---:|---:|---:|---:|
| #111 | `openai/Azure/gpt-5-mini-2025-08-07` | 10 | 3166 | 3166 | identical |
| #101 | `openai/Azure/gpt-5-mini-2025-08-07` | 50 | 15684 | 16030 | +346 |
| #102 | `openai/Azure/gpt-5-mini-2025-08-07` | 50 | 15684 | 16030 | +346 |
| #103 | `openai/Azure/gpt-5-mini-2025-08-07` | 50 | 15684 | 16030 | +346 |
| #104 | `openai/Azure/gpt-5-mini-2025-08-07` | 50 | 15684 | 16361 | +677 |
| #105 | `openai/Azure/gpt-5-mini-2025-08-07` | 50 | 15684 | 16015 | +331 |
| #106 | `openai/Azure/gpt-5-mini-2025-08-07` | 50 | 15684 | 16015 | +331 |
| #107 | `openai/Azure/gpt-5-mini-2025-08-07` | 50 | 15684 | 15684 | identical |
| #108 | `openai/Azure/gpt-5-mini-2025-08-07` | 50 | 15684 | 15684 | identical |
| #109 | `openai/Azure/gpt-5-mini-2025-08-07` | 50 | 16015 | 15684 | -331 |
| #110 | `openai/Azure/gpt-5-mini-2025-08-07` | 50 | 16015 | 15684 | -331 |
| #112 | `openai/aws/claude-sonnet-5` | 10 | 936325 | 892145 | -44180 |
| #113 | `openai/aws/claude-sonnet-5` | 10 | 931205 | 886885 | -44320 |

**The gsm8k legs are not byte-identical, within or across platforms** — worth stating plainly, because the per-platform reports' own identical-work checks flag this too. OCP (ykt3/ykt2) produced 2 distinct totals across its 10 replicate legs (8 legs share the most common one) and KinD (single node) produced 4 (4 share the most common). Within-platform spread is 2.1% (OCP (ykt3/ykt2)) and 4.3% (KinD (single node)); the largest same-leg gap across platforms is 4.3%.

The cause is benign and expected: the model is sampled rather than deterministic, so an agent occasionally spends one extra tool call on a task, and the two clusters front different LiteLLM gateways. What matters is the ratio of that confound to the effects being measured. A few percent of token drift cannot manufacture the differences below, which are **multiples** — up to 60x. The confound would matter if this document ranked presets against each other by a few percent, and it explicitly does not.

## The headline: the platforms disagree about which layer costs anything

Steady-state non-LLM time per task (`agent_call_s - llm_total_s`), replicates pooled, warm-up wave excluded.

| condition | OCP (ykt3/ykt2) median s | KinD (single node) median s | ratio | marginal step (OCP (ykt3/ykt2)) | marginal step (KinD (single node)) |
|---|---:|---:|---:|---:|---:|
| **baseline** | 0.649 | 0.117 | 5.6x | — | — |
| auth-only | 12.424 | 0.196 | 63.5x | +11.77 | +0.08 |
| ibac-only | 13.310 | 2.107 | 6.3x | +0.89 | +1.91 |
| full | 15.235 | 2.058 | 7.4x | +1.92 | -0.05 |
| full+ibac:observe | 14.125 | 1.999 | 7.1x | -1.11 | -0.06 |

Read the two *marginal step* columns against each other — that is the whole finding.

- On **OCP (ykt3/ykt2)** the cost enters at **auth-only** (`+11.77` s) and every later layer is lost in the noise.
- On **KinD (single node)** that same step is nearly free, and the cost enters at **ibac-only** (`+1.91` s) instead.

**So the two clusters do not merely scale differently — they attribute the cost to different plugin layers.** A reader of the OCP (ykt3/ykt2) report alone would conclude that the sidecar's presence is the expense and IBAC's judge call is negligible; a reader of the KinD (single node) report alone would conclude the exact opposite. Both readings are correct about their own cluster.

### It is variable cost, not a fixed penalty (a hypothesis worth falsifying)

A ~12 s per-task cost on one cluster and ~0.1 s on another invites an obvious guess: a timeout or a failing retry somewhere in the sidecar's egress path. A timeout leaves a signature — values piled on a round number with a small spread. The distributions falsify it.

| platform | condition | n | min | median | max | SD |
|---|---|---:|---:|---:|---:|---:|
| OCP (ykt3/ykt2) | baseline | 92 | 0.30 | 0.65 | 1.20 | 0.21 |
| OCP (ykt3/ykt2) | auth-only | 92 | 4.45 | 12.42 | 20.44 | 3.57 |
| OCP (ykt3/ykt2) | full | 92 | 6.05 | 15.23 | 27.84 | 4.57 |
| KinD (single node) | baseline | 92 | 0.08 | 0.12 | 0.27 | 0.04 |
| KinD (single node) | auth-only | 92 | 0.12 | 0.20 | 0.51 | 0.08 |
| KinD (single node) | full | 92 | 0.17 | 2.06 | 13.77 | 2.70 |

On OCP (ykt3/ykt2) the sidecar conditions spread across roughly an order of magnitude (4.5–20 s for `auth-only`) with an SD of ~3.6 s — **broad and unimodal, with nothing piled at a round number**. That is the shape of contention and queueing, not of a fixed timeout, so the timeout hypothesis is dead.

KinD (single node) deserves a separate remark rather than a reassuring one-liner. Its `auth-only` distribution is genuinely tight (SD 0.08 s), but its `full` distribution is **not**: median 2.06 s against a max of 13.77 s, SD 2.70 s. That heavy right tail is the IBAC judge, which is itself an LLM call and therefore inherits inference latency variance. So on the quiet cluster the judge's own variability becomes the dominant source of spread, where on the busy cluster it is buried under cluster contention.

Two consequences. First, the mechanism to chase on the shared cluster is *why the intercepted path is slow and jittery there*, not a misconfigured timeout — a different investigation. Second, on both platforms the spread grows with the median, so enabling the sidecar costs **reproducibility** as well as latency, which matters for anyone using these benchmarks to detect regressions.

## What reproduced exactly: every structural finding

The magnitudes did not travel. The *structure* did — and the structural findings are the ones with consequences.

### The serial diagnostic, independently on both clusters

`ibac-only` at `max_parallel_sessions=1`: strictly serial tool calls, so no two can race for one cache entry.

| platform | tasks attempted | serial tool calls | judge calls | ratio |
|---|---:|---:|---:|---:|
| OCP (ykt3/ykt2) | 10 | 9 | 10 | 1.11 |
| KinD (single node) | 10 | 10 | 10 | 1.00 |

**Both clusters authorize every serial call.** TTL decision caching is ruled out twice, independently — a cache with a time-to-live would have collapsed ten same-shape serial calls to roughly one judge call on *either* platform. Whatever produces the shortfall at `p=4` is therefore specific to concurrency, and the two candidates that remain (benign single-flight deduplication vs fail-open under race) produce identical counts. Separating them is a code-reading task, not a measurement task, and it is the one open item from this study with a security consequence.

### The judged-call ratio is ~1, on both

The 12-run matrices repeatedly showed IBAC legs authorizing only *some* calls — at one point 3 of 5 — which read as alarming. At n=50 it is a ratio rather than an anecdote.

| condition | OCP (ykt3/ykt2) judged/call | KinD (single node) judged/call |
|---|---:|---:|
| baseline | 0.00–0.00 | 0.00–0.00 |
| auth-only | 0.00–0.00 | 0.00–0.00 |
| ibac-only | 0.94–0.98 | 0.94–1.00 |
| full | 0.98–1.00 | 0.98–1.00 |
| full+ibac:observe | 0.98–0.98 | 0.94–0.94 |

`baseline` and `auth-only` sit at 0.00 on both platforms — correct, those presets do not engage IBAC, and a non-zero value there would have indicated the judge was being consulted by something other than the benchmark. The IBAC conditions sit near 1 on both. **The alarming 12-run ratio was small-sample noise on a quantity that is really ~1.**

### The cross-benchmark projection fails on both — in opposite directions

The cheap way to price a plugin on an expensive benchmark is to multiply a gsm8k per-tool-call delta by the target benchmark's tool-call count. That assumes per-call cost is a constant. tau2 makes ~11 tool calls per task against gsm8k's ~1, so the pair tests the assumption.

| platform | tau2 measured Δ s/task | projected from gsm8k | measured / projected |
|---|---:|---:|---:|
| OCP (ykt3/ykt2) | +70.8 | 155.0 | 0.46x |
| KinD (single node) | +46.8 | 20.7 | 2.26x |

**The projection is not merely inaccurate — it is inconsistent in sign.** It over-estimates by 2.2x on OCP (ykt3/ykt2) and under-estimates by 2.3x on KinD (single node) — the two factors landing on nearly the same magnitude is a coincidence of these two clusters, not a shared constant. A method that errs in both directions cannot be salvaged with a correction factor — so this shortcut has no defensible form and should not be used to price a benchmark. Measuring is the only way to know a benchmark's plugin cost, and it is two legs, as done here.

## The unit of replication, checked twice

Each condition ran on two independent deploys. The spread between two deploys of the *same* condition is the noise floor that any preset-to-preset claim has to clear.

| condition | OCP (ykt3/ykt2) \|Δ\| between deploys | KinD (single node) \|Δ\| between deploys |
|---|---:|---:|
| baseline | 0.038 | 0.015 |
| auth-only | 2.596 | 0.009 |
| ibac-only | 1.574 | 0.135 |
| full | 1.929 | 0.071 |
| full+ibac:observe | 1.509 | 0.553 |

Typical between-deploy \|Δ\| is **1.57 s** on OCP (ykt3/ykt2) and **0.071 s** on KinD (single node). On both platforms the noise floor scales with the platform's own cost level, and on both it **exceeds every preset-to-preset marginal step except the one dominant layer**. The conclusion is the same on each cluster and worth stating plainly: with two deploys per condition, a *ranking of presets* is not available at any task count. Adding tasks tightens the wrong interval.

## What this means for how plugin cost gets quoted

**Portable across platforms (quote these):**

- Every serial tool call is authorized; TTL caching is ruled out. Reproduced independently.
- The judged-call ratio for IBAC presets is ~1, and 0 for presets that do not engage IBAC.
- Enabling the AuthBridge sidecar has a cost that is large relative to the no-sidecar baseline and clearly resolvable on both clusters.
- Beyond the one dominant layer, preset-to-preset differences are below the between-deploy noise floor on both clusters.
- Per-tool-call cost is **not** a cross-benchmark constant.

**Not portable (do not quote without naming the cluster):**

- Any absolute per-task second figure. The same condition on the same image differs by 6–64x between these two clusters.
- *Which layer* the cost belongs to. The two clusters disagree, and each is right locally.
- Any ranking of presets against each other.

The practical rule this suggests: measure plugin overhead **on the cluster whose numbers you intend to quote**, using this spec file, and treat the structural findings as the only portable ones.

## Reproducing this

```sh
# one platform at a time -- they front the same LLM gateways, so parallel runs contend
BM_SPECS=reference/plugin_study_specs.json BM_LABEL=pstudy-<platform> \
  python3 reference/run-12.py          # detached; see feedback_long_runs_detach_and_adopt
python3 reference/gen-plugin-study-xplat.py reference/plugin_study_specs.json v1.27 plugin-study-xplat.md \
  'OCP (ykt3/ykt2)' /tmp/autobench/run12-pstudy-ocp.json /tmp/autobench/judge-ocp.ts -- \
  'KinD (single node)' /tmp/autobench/run12-pstudy-kind.json /tmp/autobench/judge-kind.ts
```
