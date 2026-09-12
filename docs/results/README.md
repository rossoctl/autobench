# Published benchmark reports

This directory holds **landmark runs only** — not every run we do. Everyday runs land in the
gitignored `results/` directory at the repo root and stay on the machine that produced them.

A run earns a place here when it establishes a reference point that later work will be compared
against, or when it overturns something we previously believed. Two examples of what that means in
practice: the v1.27 cross-cluster 12-run is the baseline that a regression would be measured
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

## v1.27 — 2026-09-11/12

The current baseline. Same Service image (`v1.27`, a verified multi-arch index) on both platforms,
same 12 request bodies, deterministic task selection, every leg deploying fresh.

| report | what it establishes |
|---|---|
| [12run-ocp.md](v1.27-2026-09-12/12run-ocp.md) | Full 12-run on OpenShift. 12/12 legs succeeded. |
| [12run-kind.md](v1.27-2026-09-12/12run-kind.md) | The same 12 runs on a single-node KinD cluster. 12/12 succeeded. |
| [12run-comparison.md](v1.27-2026-09-12/12run-comparison.md) | Like-for-like proof: 10/12 pass rates identical, 7 gsm8k input-token totals byte-identical, 0 lost token rows. |
| [plugin-study-ocp.md](v1.27-2026-09-12/plugin-study-ocp.md) | Designed AuthBridge overhead experiment, 13 legs, n=50, reversed-order replicates. |
| [plugin-study-kind.md](v1.27-2026-09-12/plugin-study-kind.md) | The same 13 legs on KinD. |
| [plugin-study-xplat.md](v1.27-2026-09-12/plugin-study-xplat.md) | **The one to read.** The two clusters disagree about *which plugin layer* costs anything, so no absolute per-task plugin figure is portable. |

## What is deliberately not here

**Earlier versions' 12-runs.** v1.26 and before are superseded by v1.27 on the same two platforms.
Keeping them would invite comparisons across an image change that nothing controls for.

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
