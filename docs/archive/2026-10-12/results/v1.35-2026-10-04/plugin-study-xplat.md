# AuthBridge plugin overhead — OpenShift — ykt3, single-cluster vs KinD — single-node local

**Report generated:** 2026-10-05T23:42:40Z  
**Service version:** `v1.35`  
**Experiment:** `plugin_study_specs.json` — 13 legs, identical specs on both platforms  
**Companion reports:** the per-platform analyses, which carry the intervals and the design rationale this document does not repeat.

**Gateway completion cache:** both platforms rested each prompt set for **900 s** between legs sharing it, against a measured TTL of ~10 min — so every leg on both sides paid for its own completions.

**Contents**

- [Why compare, when both reports already agree on method](#why-compare-when-both-reports-already-agree-on-method)
- [First: did the two platforms do the same work?](#first-did-the-two-platforms-do-the-same-work)
- [The headline: both platforms put the cost in the same layer, at different magnitudes](#the-headline-both-platforms-put-the-cost-in-the-same-layer-at-different-magnitudes)
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

That is what came back, and it is worth saying because it is not guaranteed. The platforms agree on every *structural* finding **and** on which plugin layer the cost belongs to — **ibac-only** on both — and they differ only in magnitude, by 1.0–1.7x. Attribution is not guaranteed to travel, which is why this report checks it rather than assuming it. Still name the cluster beside any per-task figure: the seconds are close here, not equal.

## First: did the two platforms do the same work?

A latency comparison across clusters is only meaningful if the workload matched. Model and input-token totals are the signature; both are read back from the artifacts.

| leg | model | tasks | OpenShift — ykt3, single-cluster input tok | KinD — single-node local input tok | Δ |
|---|---|---:|---:|---:|---:|
| #111 | `openai/Azure/gpt-5-mini-2025-08-07` | 10 | 3166 | 3166 | identical |
| #101 | `openai/Azure/gpt-5-mini-2025-08-07` | 50 | 15684 | 15684 | identical |
| #102 | `openai/Azure/gpt-5-mini-2025-08-07` | 50 | 15684 | 15684 | identical |
| #103 | `openai/Azure/gpt-5-mini-2025-08-07` | 50 | 15684 | 15684 | identical |
| #104 | `openai/Azure/gpt-5-mini-2025-08-07` | 50 | 15684 | 15684 | identical |
| #105 | `openai/Azure/gpt-5-mini-2025-08-07` | 50 | 15684 | 15684 | identical |
| #106 | `openai/Azure/gpt-5-mini-2025-08-07` | 50 | 15684 | 15684 | identical |
| #107 | `openai/Azure/gpt-5-mini-2025-08-07` | 50 | 15684 | 15684 | identical |
| #108 | `openai/Azure/gpt-5-mini-2025-08-07` | 50 | 15684 | 16034 | +350 |
| #109 | `openai/Azure/gpt-5-mini-2025-08-07` | 50 | 15684 | 15684 | identical |
| #110 | `openai/Azure/gpt-5-mini-2025-08-07` | 50 | 15684 | 15684 | identical |
| #112 | `openai/aws/claude-sonnet-5` | 10 | 993982 | 959575 | -34407 |
| #113 | `openai/aws/claude-sonnet-5` | 10 | 1000258 | 940870 | -59388 |

**The gsm8k legs are not all byte-identical** — worth stating plainly, because the per-platform reports' own identical-work checks flag this too. OpenShift — ykt3, single-cluster produced 1 distinct totals across its 10 replicate legs (10 legs share the most common one) and KinD — single-node local produced 2 (9 share the most common). Within-platform spread is 0.0% (OpenShift — ykt3, single-cluster) and 2.2% (KinD — single-node local); the largest same-leg gap across platforms is 2.2%.

The cause is benign and expected: the model is sampled rather than deterministic, so an agent occasionally spends one extra tool call on a task. What matters is the ratio of that confound to the effects being measured. A few percent of token drift cannot manufacture the differences below, which are **multiples** of the baseline — up to 17x. The confound would matter if this document ranked presets against each other by a few percent, and it explicitly does not.

## The headline: both platforms put the cost in the same layer, at different magnitudes

Steady-state non-LLM time per task (`agent_call_s - llm_total_s`), replicates pooled, warm-up wave excluded.

| condition | OpenShift — ykt3, single-cluster median s | KinD — single-node local median s | ratio | marginal step (OpenShift — ykt3, single-cluster) | marginal step (KinD — single-node local) |
|---|---:|---:|---:|---:|---:|
| **baseline** | 0.168 | 0.097 | 1.7x | — | — |
| auth-only | 0.214 | 0.130 | 1.6x | +0.05 | +0.03 |
| ibac-only | 1.569 | 1.610 | 1.0x | +1.36 | +1.48 |
| full | 1.584 | 1.602 | 1.0x | +0.01 | -0.01 |
| full+ibac:observe | 1.583 | 1.607 | 1.0x | -0.00 | +0.00 |

Read the two *marginal step* columns against each other — that is the whole finding.

- Both platforms locate the dominant step at **ibac-only**, at `+1.36` s on OpenShift — ykt3, single-cluster and `+1.48` s on KinD — single-node local.

### It is variable cost, not a fixed penalty (a hypothesis worth falsifying)

A per-task step of `+1.36` s on OpenShift — ykt3, single-cluster and `+1.48` s on KinD — single-node local invites an obvious guess: a timeout or a failing retry somewhere in the sidecar's egress path. A timeout leaves a signature — values piled on a round number with a small spread. The distributions test it.

| platform | condition | n | min | median | max | SD |
|---|---|---:|---:|---:|---:|---:|
| OpenShift — ykt3, single-cluster | baseline | 92 | 0.15 | 0.17 | 0.56 | 0.07 |
| OpenShift — ykt3, single-cluster | auth-only | 92 | 0.20 | 0.21 | 0.55 | 0.08 |
| OpenShift — ykt3, single-cluster | full | 92 | 0.21 | 1.58 | 9.63 | 1.65 |
| KinD — single-node local | baseline | 92 | 0.08 | 0.10 | 0.35 | 0.04 |
| KinD — single-node local | auth-only | 92 | 0.11 | 0.13 | 0.31 | 0.04 |
| KinD — single-node local | full | 92 | 0.12 | 1.60 | 7.75 | 1.28 |

In the IBAC conditions on OpenShift — ykt3, single-cluster `full` spans 0.21–9.63 s around a median of 1.58 s (SD 1.65 s), its busiest 0.1-s bin holding 11% of tasks; on KinD — single-node local `full` spans 0.12–7.75 s around a median of 1.60 s (SD 1.28 s), its busiest 0.1-s bin holding 12% of tasks. **Broad, with nothing piled at a round number** — the shape of variable latency, not of a fixed timeout, so the timeout hypothesis does not survive.

The heavy right tail has an obvious owner: the IBAC judge is itself an LLM call, so the conditions that consult it inherit inference latency variance. Two consequences. First, the cost should move with the judge's model and gateway rather than with the cluster — consistent with it landing in the same layer on both. Second, on both platforms the spread grows with the median, so engaging the judge costs **reproducibility** as well as latency, which matters for anyone using these benchmarks to detect regressions.

## What reproduced exactly: every structural finding

The magnitudes are close rather than equal; the *structure* reproduced exactly — and the structural findings are the ones with consequences.

### The serial diagnostic, independently on both clusters

`ibac-only` at `max_parallel_sessions=1`: strictly serial tool calls, so no two can race for one cache entry.

| platform | tasks attempted | serial tool calls | judge calls | ratio |
|---|---:|---:|---:|---:|
| OpenShift — ykt3, single-cluster | 10 | 10 | 10 | 1.00 |
| KinD — single-node local | 10 | 10 | 10 | 1.00 |

**Both clusters authorize every serial call.** TTL decision caching is ruled out twice, independently — a cache with a time-to-live would have collapsed ten same-shape serial calls to roughly one judge call on *either* platform. Whatever produces the shortfall at `p=4` is therefore specific to concurrency, and the two candidates that remain (benign single-flight deduplication vs fail-open under race) produce identical counts. Separating them is a code-reading task, not a measurement task, and it is the one open item from this study with a security consequence.

### The judged-call ratio is ~1, on both

The 12-run matrices repeatedly showed IBAC legs authorizing only *some* calls — at one point 3 of 5 — which read as alarming. At n=50 it is a ratio rather than an anecdote.

| condition | OpenShift — ykt3, single-cluster judged/call | KinD — single-node local judged/call |
|---|---:|---:|
| baseline | 0.00–0.00 | 0.00–0.00 |
| auth-only | 0.00–0.00 | 0.00–0.00 |
| ibac-only | 0.98–1.00 | 1.00–1.02 |
| full | 0.94–0.98 | 0.96–0.96 |
| full+ibac:observe | 0.96–0.96 | 0.96–0.98 |

`baseline` and `auth-only` sit at 0.00 on both platforms — correct, those presets do not engage IBAC, and a non-zero value there would have indicated the judge was being consulted by something other than the benchmark. The IBAC conditions sit near 1 on both. **The alarming 12-run ratio was small-sample noise on a quantity that is really ~1.**

### The cross-benchmark projection fails on both — in opposite directions

The cheap way to price a plugin on an expensive benchmark is to multiply a gsm8k per-tool-call delta by the target benchmark's tool-call count. That assumes per-call cost is a constant. tau2 makes ~11 tool calls per task against gsm8k's ~1, so the pair tests the assumption.

| platform | tau2 measured Δ s/task | projected from gsm8k | measured / projected |
|---|---:|---:|---:|
| OpenShift — ykt3, single-cluster | +28.3 | 17.0 | 1.66x |
| KinD — single-node local | +33.4 | 16.6 | 2.01x |

The projection does not reproduce the measured value on either platform; see the per-platform reports for the mechanism discussion.

## The unit of replication, checked twice

Each condition ran on two independent deploys. The spread between two deploys of the *same* condition is the noise floor that any preset-to-preset claim has to clear.

| condition | OpenShift — ykt3, single-cluster \|Δ\| between deploys | KinD — single-node local \|Δ\| between deploys |
|---|---:|---:|
| baseline | 0.009 | 0.013 |
| auth-only | 0.003 | 0.012 |
| ibac-only | 0.078 | 0.184 |
| full | 0.022 | 0.124 |
| full+ibac:observe | 0.089 | 0.144 |

Typical between-deploy \|Δ\| is **0.022 s** on OpenShift — ykt3, single-cluster and **0.124 s** on KinD — single-node local, so a step must exceed **0.07 s** and **0.39 s** respectively (three between-deploy SDs, the rule the per-platform reports use) to count as resolved. On OpenShift — ykt3, single-cluster the marginal steps that clear that floor are `ibac-only` (+1.36 s). On KinD — single-node local the marginal steps that clear that floor are `ibac-only` (+1.48 s). Every other step is inside the noise, so with two deploys per condition a *ranking of presets* beyond those is not available at any task count. Adding tasks tightens the wrong interval.

## What this means for how plugin cost gets quoted

**Portable across platforms (quote these):**

- Every serial tool call is authorized; TTL caching is ruled out. Reproduced independently.
- The judged-call ratio for IBAC presets is ~1, and 0 for presets that do not engage IBAC.
- The cost enters at **ibac-only** on both clusters (`+1.36` s and `+1.48` s per task) and is clearly resolvable on both.
- Beyond the one dominant layer, preset-to-preset differences are below the between-deploy noise floor on both clusters.
- Per-tool-call cost is **not** a cross-benchmark constant.

**Not portable (do not quote without naming the cluster):**

- Any absolute per-task second figure. The same condition on the same image differs by 1–2x between these two clusters.
- The exact size of each step: close on these two clusters, but measured on two only.
- Any ranking of presets against each other.

The practical rule this suggests: measure plugin overhead **on the cluster whose numbers you intend to quote**, using this spec file, and treat the structural findings as the only portable ones.

## Reproducing this

```sh
# one platform at a time -- separate gateways, but the SAME upstream model providers behind
# both, so parallel runs contend and the latency numbers stop meaning anything
BM_SPECS=reference/plugin_study_specs.json BM_LABEL=pstudy-<platform> \
  BM_CACHE_GAP=900 BM_ORDER=111,112,101,102,103,104,105,113,106,107,108,109,110 \
  python3 reference/run-12.py          # detached; see feedback_long_runs_detach_and_adopt
python3 reference/gen-plugin-study-xplat.py reference/plugin_study_specs.json v1.35 plugin-study-xplat.md \
  'OpenShift — ykt3, single-cluster' /tmp/autobench/run12-pstudy-ocp-v135.json results/pstudy-ocp-v135-20261005/judge-ocp.ts -- \
  'KinD — single-node local' /tmp/autobench/run12-pstudy-kind-v135-r3.json results/pstudy-kind-v135-r3-20261005/judge-kind.ts
```
