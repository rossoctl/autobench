# Published benchmark reports

This directory holds **landmark runs only** — not every run we do. Everyday runs land in the
gitignored `results/` directory at the repo root and stay on the machine that produced them.

A run earns a place here when it establishes a reference point that later work will be compared
against, or when it overturns something we previously believed. Two examples of what that means in
practice: the current cross-cluster 12-run is the baseline that a regression would be measured
against, and the plugin-overhead study exists because it falsified a method we had been quoting.
A run that merely repeats an established result does not earn a place here, however clean it is.

Everything in this directory is **generated**, never hand-edited. Each report ends with the command
that reproduces it. If a number looks wrong, fix the generator in `reference/` and regenerate —
editing the Markdown would make the report disagree with the artifacts it claims to summarize.

## Reading order for a newcomer

Start with [docs/BENCHMARKS_PRIMER.md](../BENCHMARKS_PRIMER.md) for what the three benchmarks
actually ask a model to do, and [docs/PLUGIN_OVERHEAD.md](../PLUGIN_OVERHEAD.md) for the plugin
vocabulary and the findings in plain prose. The raw reports below are dense on purpose; the two
curated documents carry the conclusions.

## v1.28 — 2026-09-15

**The current baseline, and the only one.** Same Service image (`v1.28`) on both platforms, same 12
request bodies, deterministic task selection, every leg deploying fresh. Two things make it a
reference point rather than one more matrix:

- **The agent's health-probe defect is gone.** The v1.28 legs ran `exgentic 0.3.5.dev146+gff7ef6a37`,
  which fixed the per-task `GET /v1/models` probe that had killed **12 of 141 KinD tasks** on
  `dev145`. Both matrices now record **zero** probe failures, so pass rates need no
  `total − probe_failures` denominator and every failure left in them is a real one.
- **Legs that share prompts were spaced past the gateway's completion cache.** The cache TTL was
  measured at ~10 minutes, and `BM_CACHE_GAP=900` rests each (benchmark, model) prompt set beyond it,
  so **per-call latency and output tokens are independent across legs for the first time** — the
  caveat every earlier matrix carried. An interleaved `BM_ORDER` kept the cost at 33 minutes of
  sleeping per platform instead of 165.

| report | what it establishes |
|---|---|
| [12run-ocp.md](v1.28-2026-09-15/12run-ocp.md) | Full 12-run on OpenShift. 12/12 legs succeeded, 141 tasks, 0 lost token rows. |
| [12run-kind.md](v1.28-2026-09-15/12run-kind.md) | The same 12 runs on a single-node KinD cluster. 12/12 succeeded, 141 tasks, 0 lost token rows. |
| [12run-comparison.md](v1.28-2026-09-15/12run-comparison.md) | **The one to read.** 7/12 pass rates identical, 6 gsm8k input-token totals byte-identical, and every errored task classified by *cause* — so a pass-rate delta can be read against "wrong answer" (OCP 2, KinD 1) rather than against timeouts and a dropped socket. |
| [plugin-study-ocp.md](v1.28-2026-09-15/plugin-study-ocp.md) | Designed AuthBridge overhead experiment on OpenShift — 13 legs, n=50, reversed-order replicates, cache spaced. |
| [plugin-study-kind.md](v1.28-2026-09-15/plugin-study-kind.md) | The same 13 legs on KinD. |
| [plugin-study-xplat.md](v1.28-2026-09-15/plugin-study-xplat.md) | **The one to read for plugins.** The two clusters agree on every structural finding and disagree on every magnitude — including *which* layer costs anything (OpenShift the sidecar at +13.68 s/task, KinD the judge at +1.54 s). No absolute per-task plugin figure is portable. |

One note on the pass rates: **7 of 12 are identical across the two platforms**, and all five
differences favour KinD. On a 5-task leg one task is worth 0.20, and tau2 and appworld are
nondeterministic per episode, so read the comparison's per-*cause* table rather than the rate — wrong
answers split 2 (OCP) to 1 (KinD), while timeouts split 4 to 11 and are all appworld. The single
**transport failure** (OCP #6, a connection closed mid-stream) was a one-off in the network; the
runner did not retry transport failures when these legs ran, and now does (`send_prompt` in
`runner/a2a_agent.py`), so a repeat would self-heal rather than cost a task.

The three **plugin-study** reports are the designed AuthBridge experiment on this same image with the
cache spaced out — 13 legs per platform, 26 legs total, all `succeeded`. What the spacing buys is
worth knowing: the between-deploy noise floor on OpenShift is **0.23 s**, tight enough for the study
to say the sub-second layers are genuinely small rather than merely under-replicated; the serial
diagnostic lands on a clean 10 tool calls / 10 judge calls on *both* platforms; and each report
verifies the nesting invariant from its own artifacts — no leg carrying the sidecar is faster than a
leg without one — which is the cheap check that a latency comparison of a nested design has not been
served out of a cache. The headline finding is the disagreement: the same condition on the same image
differs by **4–93x** between the two clusters.

## What is deliberately not here

**Earlier versions' 12-runs and plugin studies.** v1.27 and before are superseded on the same two
platforms, and keeping them would invite comparisons across an image change that nothing controls
for. The earlier plugin study has a second and stronger reason to be gone: it ran before the gateway's
completion cache was understood, with no spacing, so all eleven of its gsm8k legs sent the *same* 50
prompts inside the ~10 min TTL and the later ones were served the earlier ones' completions. For a
study whose outcome *is* latency that is the measurement disappearing, in run order, which is the
same shape as a plugin effect — and the crossover design cannot absorb it, because the warming is
monotone in run order and the judge is itself a call through that same gateway. Its per-layer figures
were **withdrawn rather than corrected**, and none of its numbers are quoted anywhere in this repo.
The v1.28 study above is the same design executed with the cache spaced out, and it verifies the
nesting invariant from its own artifacts so the defect cannot recur unnoticed.

**The `dev145` re-run of 2026-09-14.** It looked like a baseline and was not: the agent's per-task
health probe killed 12 of 141 tasks on KinD and 0 on OpenShift, and a killed task still leaves a
zero-token `report.ndjson` row that skews every per-task statistic. v1.28 above is that experiment
run again on the fixed agent.

**The retrospective `12run-plugin-overhead-*.md` reports.** These mined plugin cost out of the
canonical 12-run, which runs each preset once at `max_tasks=5` in a fixed order. That design cannot
resolve the question, and the reports carry a cross-benchmark projection that the designed study
above **falsified in both directions** (over-estimating by 2.2x on one cluster, under-estimating by
2.2x on the other). Publishing them would publish a method we have withdrawn. The designed study
replaces them.

## A caution about the S3 keys

The reports cite S3 object keys so that any number can be traced back to the artifact it came from.
That bucket is readable **and listable anonymously** — treat anything written there as public. The
keys embed the caller's username and the Keycloak issuer host by design; no artifact contains task
prompts or model outputs. Service endpoints are scrubbed from these reports (the generators emit a
platform label instead), because those are not public.
