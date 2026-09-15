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

## v1.28 — 2026-09-15

**The current baseline.** Same Service image (`v1.28`) on both platforms, same 12 request bodies,
deterministic task selection, every leg deploying fresh. Two things make this the reference rather
than a repeat of v1.27:

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

Two notes for anyone comparing this against v1.27. **Pass-rate agreement went down, not up** —
7 of 12 identical against v1.27's 10 of 12 — and all five differences favour KinD; on 5-task legs one
task is 0.20, and tau2/appworld are nondeterministic per episode, so this is within what these run
sizes can resolve. And the single **transport failure** (OCP #6, a connection closed mid-stream) was
a one-off in the network; the runner did not retry transport failures when these legs ran, and now
does (`send_prompt` in `runner/a2a_agent.py`), so a repeat of that failure would self-heal rather
than cost a task.

## v1.27 — 2026-09-11/12

The previous baseline, kept because the plugin-overhead study was run against it and its conclusions
are still current. Same Service image (`v1.27`, a verified multi-arch index) on both platforms, same
12 request bodies, deterministic task selection, every leg deploying fresh.

| report | what it establishes |
|---|---|
| [12run-ocp.md](v1.27-2026-09-12/12run-ocp.md) | Full 12-run on OpenShift. 12/12 legs succeeded. |
| [12run-kind.md](v1.27-2026-09-12/12run-kind.md) | The same 12 runs on a single-node KinD cluster. 12/12 succeeded. |
| [12run-comparison.md](v1.27-2026-09-12/12run-comparison.md) | Like-for-like proof: 10/12 pass rates identical, 7 gsm8k input-token totals byte-identical, 0 lost token rows. Superseded by v1.28 above for the agent fix and the cache spacing. |
| [plugin-study-ocp.md](v1.27-2026-09-12/plugin-study-ocp.md) | Designed AuthBridge overhead experiment, 13 legs, n=50, reversed-order replicates. |
| [plugin-study-kind.md](v1.27-2026-09-12/plugin-study-kind.md) | The same 13 legs on KinD. |
| [plugin-study-xplat.md](v1.27-2026-09-12/plugin-study-xplat.md) | **The one to read.** The two clusters disagree about *which plugin layer* costs anything, so no absolute per-task plugin figure is portable. |

## What is deliberately not here

**Earlier versions' 12-runs.** v1.26 and before are superseded on the same two platforms. Keeping
them would invite comparisons across an image change that nothing controls for. v1.27's 12-run is the
one exception — the plugin-overhead study beside it was run on that image, so the matrix it belongs to
stays.

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
