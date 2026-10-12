# Published benchmark reports

This directory holds **landmark runs only** — not every run we do. Everyday runs land in the
gitignored `results/` directory at the repo root and stay on the machine that produced them.

A run earns a place here when it establishes a reference point that later work will be compared
against, or when it overturns something we previously believed. The 12-run pair below is the
baseline a regression would be measured against. A run that merely repeats an established result
does not earn a place here, however clean it is.

Everything in this directory is **generated**, never hand-edited. Each report ends with the command
that reproduces it. If a number looks wrong, fix the generator in `reference/` and regenerate —
editing the Markdown would make the report disagree with the artifacts it claims to summarize.

## Reading order for a newcomer

Start with [docs/BENCHMARKS_PRIMER.md](../BENCHMARKS_PRIMER.md) for what the three benchmarks
actually ask a model to do, and [docs/PLUGIN_OVERHEAD.md](../PLUGIN_OVERHEAD.md) for the plugin
vocabulary and the findings in plain prose. The raw reports below are dense on purpose; the two
curated documents carry the conclusions.

## v1.36 — 2026-10-12

**The baseline.** AutoBench `v1.36` on two single-cluster installs on Rossoctl v0.8.0: OpenShift (the
Service and its workloads together on ykt3) and a single-node KinD cluster. Both were installed fresh
by `reference/autobench-install.sh`.
- **Like-for-like:** both ran the same 12 request bodies and per-leg model ids through **one LLM
  gateway** (`--gateway same`), with deterministic task selection and every leg deploying fresh.
- **Cache spacing:** legs that share prompts were spaced past the gateway's ~10 min completion cache
  (`BM_CACHE_GAP=900`).
- **Sequence:** OpenShift started 21 minutes after KinD finished, so neither side can have replayed
  the other's completions.

| report | what it establishes |
|---|---|
| [12run-ocp.md](v1.36-2026-10-12/12run-ocp.md) | Full 12-run on OpenShift (ykt3, single-cluster). 12/12 legs succeeded, 141 tasks, 0 lost token rows. |
| [12run-kind.md](v1.36-2026-10-12/12run-kind.md) | The same 12 runs on a single-node KinD cluster. 12/12 succeeded, 141 tasks, 0 lost token rows. |
| [12run-comparison.md](v1.36-2026-10-12/12run-comparison.md) | **The one to read.** 9/12 pass rates identical, 7 legs with byte-identical input-token totals, and every errored task classified by cause. |

What makes it a reference point:

- **Nothing was lost to infrastructure.** No task died to the agent's health probe, to the gateway,
  to the judge, or to transport, on either platform. Every failure left is the agent's or the
  model's:
  - the agent's own defect (3 tasks on OpenShift, 2 on KinD);
  - one wrong answer on OpenShift;
  - appworld tasks that reached their 600 s budget (1 on OpenShift, 2 on KinD).
- **Token attribution is complete.** Every gsm8k task on both platforms carries its own
  input-token fingerprint — #3's 50 tasks at 4-way parallelism match #2's ten run one at a time, one
  for one — and 0 rows lost their token attribution.

The three legs that differ (#7, #9, #10) are each one task apart, in both directions: on a 5-task
leg one task is worth 0.20, and tau2 is nondeterministic per episode.

Two things to know before reading its numbers:

- **Latency was measured on a slow day for the gateway.** The median model call took 2.0× as long as
  in the v1.35 pair on gsm8k, 3.0× on tau2 and 1.2× on appworld, on both platforms alike. The time
  the Service spends around each task was unchanged. So wall times are this day's, while tokens,
  dollars and pass rates are comparable across versions.
- **KinD's leg #8 was re-run.** Its first run passed 5/5, but its S3 export was refused for clock
  skew: the host slept through the cache gap, and the KinD VM's clock lagged on wake. No artifacts
  existed, so the leg was re-run alone; the KinD report names both runs.

## v1.35 — 2026-10-04: the plugin study

The designed AuthBridge plugin-overhead study ran on 2026-10-05 on image v1.35, both platforms one
after the other, with the gateway cache spaced. It is still the published plugin study: no v1.36
study has been run, and v1.36 changes nothing on the plugin path.

| report | what it establishes |
|---|---|
| [plugin-study-ocp.md](v1.35-2026-10-04/plugin-study-ocp.md) | Designed AuthBridge overhead experiment on OpenShift — 13 legs, n=50, reversed-order replicates, cache spaced, judge calls counted. |
| [plugin-study-kind.md](v1.35-2026-10-04/plugin-study-kind.md) | The same 13 legs on KinD. |
| [plugin-study-xplat.md](v1.35-2026-10-04/plugin-study-xplat.md) | **The one to read for plugins.** Both clusters put the cost in the same layer — the IBAC judge, +1.36 s/task on OpenShift and +1.48 s on KinD — and every other layer is below the between-deploy noise floor. |

Every serial tool call was judged (10 of 10 on both), the judged-call ratio is ~1 for the IBAC
presets and 0 for the rest, and per-tool-call cost does **not** carry across benchmarks (tau2 cost
1.7–2.0× what a gsm8k-based projection predicts). On KinD, one leg (#107) was re-run alone after its
first deploy never stabilised, so that replicate ran last rather than in its crossover slot; the
KinD report records it. The v1.35 12-run pair it sat beside is archived in
[`docs/archive/2026-10-12/`](../archive/2026-10-12/README.md).

## What is deliberately not here

**Earlier versions.** The v1.36 12-run pair and the v1.35 plugin study are the only published sets.
Earlier reports, and the documents built on them, are kept as frozen snapshots: the v1.35 12-run
pair in [`docs/archive/2026-10-12/`](../archive/2026-10-12/), and v1.28 and v1.33 in
[`docs/archive/2026-10-04/`](../archive/2026-10-04/). Comparing across an image change that nothing
controls for invites conclusions nobody can check.

**Plugin overhead mined out of the 12-run.** The canonical 12-run runs each AuthBridge preset once,
at `max_tasks=5`, in a fixed order, and that design cannot resolve what a plugin costs.
`reference/gen-plugin-overhead.py` still exists because its source comments record why; a designed
study with replicates is the only kind published here.

## A caution about the S3 keys

The reports cite S3 object keys so that any number can be traced back to the artifact it came from.
That bucket is readable **and listable anonymously** — treat anything written there as public. The
keys embed the caller's username and the Keycloak issuer host by design; no artifact contains task
prompts or model outputs. Service endpoints are scrubbed from these reports (the generators emit a
platform label instead), because those are not public.
