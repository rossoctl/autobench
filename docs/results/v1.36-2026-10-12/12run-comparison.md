# 12-Run Comparison — OpenShift (ykt3, single-cluster) vs KinD (single-node local), both Service v1.36

**Report generated:** 2026-10-12T22:26:26Z

**Platforms compared:** OpenShift (ykt3, single-cluster) vs KinD (single-node local)

Both sides ran the same 12 request bodies, the same Service version, and verified-identical
instance config. Every leg deploys fresh. Task selection is deterministic, so the same
`task_id` is the same task on both platforms, which makes the two sides comparable.

**That does not make every difference a property of the platform.** A task can also be lost to
the agent's own per-task health probe before it ever reaches the model, and such a task is
scored as not-passed — so a raw `pass_rate` mixes "the agent got it wrong" with "the agent
never ran". Where that happened it is counted and separated below; read the adjusted column
before attributing anything to the cluster.

All numbers are derived from the mirrored `report.ndjson` and `run.json` artifacts.

**Contents**

- [S3 artifact locations](#s3-artifact-locations)
- [Pass rate, wall time, tokens](#pass-rate-wall-time-tokens)
- [Why tasks failed](#why-tasks-failed)
- [Token distribution per task](#token-distribution-per-task)
  - [Input tokens](#input-tokens)
  - [Output tokens](#output-tokens)
- [Tool-selection calls](#tool-selection-calls)
- [Totals](#totals)
- [What matches](#what-matches)
- [Where they differ](#where-they-differ)
- [Caveats on the `llm` column and on token totals](#caveats-on-the-llm-column-and-on-token-totals)
- [Reproducing this report](#reproducing-this-report)

## S3 artifact locations

Every number below is derived from artifacts published to S3. Both sides share one bucket and differ only in the key prefix (the instance's configured `s3.prefix` and encoded issuer host).

| | |
|---|---|
| **URL root** | `https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/` |
| **OpenShift (ykt3, single-cluster) key prefix** (96 objects) | `ykt3/benchmarker/keycloak-keycloak.apps.ykt3.hcp.res.ibm.com-realms-rossoctl/` |
| **KinD (single-node local) key prefix** (96 objects) | `kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/` |

Key layout is `<s3.prefix>/<caller>/<iss-host-encoded>/<benchmark>/<run_id>/<artifact>`, so **benchmark selects cleanly by prefix** but **the model in use does not appear in the key at all** — gsm8k mixes models across its runs, so no prefix separates them. Resolve model -> `run_id` from the pass-rate table below when you need per-model objects.

| benchmark | objects OpenShift (ykt3, single-cluster) | objects KinD (single-node local) | sub-prefix |
|---|---:|---:|---|
| appworld | 16 | 16 | `appworld/` |
| gsm8k | 64 | 64 | `gsm8k/` |
| tau2 | 16 | 16 | `tau2/` |

Objects are readable **and listable anonymously**, so treat anything written here as public. What is exposed: the **keys** carry the caller's username and the Keycloak issuer host, and `run.json` / `report.ndjson` can carry **exception strings** from failed tasks. No artifact contains task prompts or model outputs — the record schema is ids, counts and durations, and `span_report.*` publishes only a fixed whitelist of structural/numeric span fields.

## Pass rate, wall time, tokens

| # | bench | p | pass OpenShift (ykt3, single-cluster) | pass KinD (single-node local) | Δ | wall OpenShift (ykt3, single-cluster) | wall KinD (single-node local) | KinD (single-node local)/OpenShift (ykt3, single-cluster) | in OpenShift (ykt3, single-cluster) | in KinD (single-node local) | in Δ |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | gsm8k | 1 | 1.0 | 1.0 | +0.00 | 7 | 7 | 1.11x | 320 | 320 | same |
| 2 | gsm8k | 1 | 1.0 | 1.0 | +0.00 | 64 | 45 | 0.71x | 3166 | 3166 | same |
| 3 | gsm8k | 4 | 1.0 | 1.0 | +0.00 | 80 | 44 | 0.55x | 15684 | 15684 | same |
| 4 | gsm8k | 4 | 0.8 | 0.8 | +0.00 | 21 | 19 | 0.89x | 4404 | 3536 | -868 |
| 5 | gsm8k | 4 | 1.0 | 1.0 | +0.00 | 9 | 14 | 1.48x | 1564 | 1564 | same |
| 6 | gsm8k | 4 | 1.0 | 1.0 | +0.00 | 16 | 14 | 0.90x | 1564 | 1564 | same |
| 7 | gsm8k | 4 | 0.8 | 1.0 | +0.20 | 16 | 12 | 0.74x | 1564 | 1564 | same |
| 8 | gsm8k | 4 | 1.0 | 1.0 | +0.00 | 14 | 17 | 1.18x | 1564 | 1564 | same |
| 9 | tau2 | 1 | 0.7 | 0.9 | +0.20 | 933 | 705 | 0.76x | 950408 | 965083 | +14675 |
| 10 | tau2 | 4 | 0.9 | 0.8 | -0.10 | 765 | 877 | 1.15x | 1722097 | 1709751 | -12346 |
| 11 | appworld | 1 | 0.0 | 0.0 | +0.00 | 2280 | 1629 | 0.71x | 998804 | 911103 | -87701 |
| 12 | appworld | 4 | 0.0 | 0.0 | +0.00 | 1513 | 1782 | 1.18x | 5349737 | 5377703 | +27966 |

## Why tasks failed

Every task that ended with an error, bucketed by what kind of thing went wrong. This is the
context a pass-rate delta needs: a task lost to a socket closing and a task lost to the
model answering wrongly sit in the same `evaluated_pass` denominator and are
indistinguishable in every table above, but only one of them is a statement about the
agent. Counted from `run.json`, which carries one result per task unconditionally — a task
killed by a per-task timeout leaves no `report.ndjson` row at all.

| cause | OpenShift (ykt3, single-cluster) | KinD (single-node local) | what it means |
|---|---:|---:|---|
| per-task timeout | 1 | 2 | The task exceeded its `task_timeout_seconds`. On appworld this is the dominant failure mode and is an upstream agent behaviour, not a resource limit — see the appworld notes in the per-platform reports. |
| agent (upstream defect) | 3 | 2 | The agent failed on its own — a malformed or empty completion, or its cloudpickle `_asyncio.Task` race. An upstream defect; the task never had a chance to be scored. |
| wrong answer | 1 | 0 | The agent ran, answered, and the answer was rejected. **The only bucket that is a genuine statement about the model's ability.** |

**No task on either side was lost to an infrastructure failure** (transport, gateway, judge or MCP), so every pass-rate delta below is attributable to the agent, the model or the benchmark.

## Token distribution per task

For each direction: `median` (robust centre), then `mean`, then `CV` (population sigma / mean — dimensionless, so spread is comparable across benchmarks whose token counts differ by orders of magnitude). A mean well above the median means right-skew: a few long tasks dominate. CV shares its denominator with `mean`, not `median`, and is `—` for single-task runs.

**Which direction varies more is benchmark-dependent, and output usually wins** — measured on *these* matrices at OUT CV > IN CV in **17 of the 22 leg-sides** that ran more than one task (one leg on one platform; a single-task run has no CV). By benchmark, and by model where a benchmark ran more than one: **gsm8k** IN 0.06–0.09 vs OUT 0.48–0.94 on `gpt-5-mini-2025-08-07`, IN 0.31–0.38 vs OUT 0.36–0.62 on `gpt-4.1`; **tau2** IN 0.08–0.26 vs OUT 0.16–0.32; **appworld** IN 0.37–0.91 vs OUT 0.23–0.66. The mechanism is visible in the gsm8k rows: a model that answers in one call re-sends a nearly constant prompt, so only its answer length swings, while a model that needs tool round-trips varies on the input side too — same five tasks, different shape. Only long-horizon **appworld** is input-led in every one of its leg-sides: there the tasks differ enormously in turn count and every call re-sends the whole conversation, so compounding context dominates the input side.

### Input tokens

| # | bench | median OpenShift (ykt3, single-cluster) | mean OpenShift (ykt3, single-cluster) | CV OpenShift (ykt3, single-cluster) | median KinD (single-node local) | mean KinD (single-node local) | CV KinD (single-node local) |
|---|---|---:|---:|---:|---:|---:|---:|
| 1 | gsm8k | 320 | 320 | — | 320 | 320 | — |
| 2 | gsm8k | 312 | 317 | 0.08 | 312 | 317 | 0.08 |
| 3 | gsm8k | 310 | 314 | 0.06 | 310 | 314 | 0.06 |
| 4 | gsm8k | 809 | 881 | 0.38 | 807 | 707 | 0.31 |
| 5 | gsm8k | 306 | 313 | 0.09 | 306 | 313 | 0.09 |
| 6 | gsm8k | 306 | 313 | 0.09 | 306 | 313 | 0.09 |
| 7 | gsm8k | 306 | 313 | 0.09 | 306 | 313 | 0.09 |
| 8 | gsm8k | 306 | 313 | 0.09 | 306 | 313 | 0.09 |
| 9 | tau2 | 93029 | 95041 | 0.11 | 94281 | 96508 | 0.08 |
| 10 | tau2 | 91360 | 86105 | 0.26 | 83994 | 85488 | 0.22 |
| 11 | appworld | 218414 | 249701 | 0.37 | 150656 | 227776 | 0.91 |
| 12 | appworld | 213906 | 267487 | 0.70 | 213487 | 283037 | 0.66 |

### Output tokens

| # | bench | median OpenShift (ykt3, single-cluster) | mean OpenShift (ykt3, single-cluster) | CV OpenShift (ykt3, single-cluster) | median KinD (single-node local) | mean KinD (single-node local) | CV KinD (single-node local) |
|---|---|---:|---:|---:|---:|---:|---:|
| 1 | gsm8k | 86 | 86 | — | 150 | 150 | — |
| 2 | gsm8k | 182 | 208 | 0.68 | 118 | 163 | 0.63 |
| 3 | gsm8k | 150 | 163 | 0.66 | 150 | 183 | 0.71 |
| 4 | gsm8k | 51 | 60 | 0.36 | 47 | 58 | 0.62 |
| 5 | gsm8k | 86 | 163 | 0.76 | 86 | 214 | 0.93 |
| 6 | gsm8k | 86 | 163 | 0.94 | 86 | 201 | 0.71 |
| 7 | gsm8k | 86 | 150 | 0.66 | 86 | 163 | 0.63 |
| 8 | gsm8k | 406 | 406 | 0.48 | 279 | 265 | 0.64 |
| 9 | tau2 | 2369 | 2320 | 0.20 | 2268 | 2294 | 0.16 |
| 10 | tau2 | 2047 | 2022 | 0.32 | 1820 | 1906 | 0.28 |
| 11 | appworld | 23400 | 24868 | 0.23 | 21909 | 24172 | 0.66 |
| 12 | appworld | 24254 | 25720 | 0.40 | 26958 | 27861 | 0.48 |

## Tool-selection calls

Not every LLM call works on the task. The agent image defaults to `enable_tool_shortlisting = True, max_selected_tools = 30`: above that many advertised tools, each turn opens with an extra call that carries the whole tool inventory and asks the model to rank it, and only the winners' schemas reach the call that acts. Both kinds are inside the `llm` counts and the token totals above, so the share is worth knowing before reading either — and because it is a property of the tool surface rather than of the cluster, the two sides are a check on each other.

| bench | calls/task OpenShift (ykt3, single-cluster) | select OpenShift (ykt3, single-cluster) | IN% OpenShift (ykt3, single-cluster) | calls/task KinD (single-node local) | select KinD (single-node local) | IN% KinD (single-node local) |
|---|---:|---:|---:|---:|---:|---:|
| gsm8k | 1.1 | 0.0 (0%) | 0% | 1.1 | 0.0 (0%) | 0% |
| tau2 | 11.1 | 0.0 (0%) | 0% | 11.3 | 0.0 (0%) | 0% |
| appworld | 27.9 | 14.0 (50%) | 68% | 28.1 | 14.0 (50%) | 67% |

`gsm8k` and `tau2` stay at **zero** on both sides — they advertise fewer tools than the threshold, so shortlisting cannot fire, which is the control that keeps ordinary multi-turn traffic from being counted as selection. `appworld` spends **half its calls** selecting on both platforms, and the input-token share agrees to within a few points (68% on OpenShift (ykt3, single-cluster) vs 67% on KinD (single-node local)). So the selection overhead is a property of the tool surface, not of the cluster: it inflates both sides' appworld token totals equally and does not bias the comparison — but it does mean a per-task token or dollar figure for that benchmark is mostly the price of choosing tools.

## Totals

- input tokens: OpenShift (ykt3, single-cluster) 9,050,876 · KinD (single-node local) 8,992,602
- output tokens: OpenShift (ykt3, single-cluster) 692,537 · KinD (single-node local) 702,581
- wall: OpenShift (ykt3, single-cluster) 5717s · KinD (single-node local) 5165s
- rows with lost token attribution: OpenShift (ykt3, single-cluster) 0/140 · KinD (single-node local) 0/139  — **token capture complete on both sides**
- tasks lost to the health probe: OpenShift (ykt3, single-cluster) 0/141 · KinD (single-node local) 0/141  — **every task reached the model on both sides**

## What matches

- **9 of 12 pass rates identical**: #1, #12, #2, #3, #11, #5, #6, #4, #8.
- **7 runs have byte-identical input-token totals**: #1, #2, #3, #5, #6, #7, #8 — deterministic task selection plus the same model
  means identical work, the strongest available check that this is a like-for-like comparison
  rather than merely a similar one.

## Where they differ

- **3 of 12 legs differ**: #10 (-0.10), #9 (+0.20), #7 (+0.20).
- Interpret small deltas on small runs with care: one task on a 5-task run moves pass_rate by 0.20. tau2 and appworld are additionally nondeterministic per episode.

## Caveats on the `llm` column and on token totals

**The `llm` column counts real LLM calls one-for-one on both sides, with no probe offset to subtract** — measured: no `chat` span in either matrix carries `max_tokens=1` (1100 and 1079 spans checked). Agents up to `0.3.5.dev131` issued a `max_tokens=1` capability probe that was recorded as a `chat` span, so counts from *those* runs read one high per task; as of `0.3.5.dev145` it is *replaced* by the unbilled `GET /v1/models` check, which emits no span. Do not compare `llm` counts across that boundary: a leg looks like it made one fewer call per task when only the instrumentation changed.

The damage detector used above is unchanged and stays structural — `llm <= 1` alongside
`tool >= 2` is impossible, because every tool call needs a preceding model turn. A bare
"one `chat` span" test is now actively wrong: one `chat` span is the *healthy* shape for a
one-shot gsm8k task. `tokens == 0` was never sufficient either — while the probe existed it
was the span that *survived* the loss, carrying its own usage on non-reasoning models
(`claude-sonnet-5` `in=8/out=1`, `gemini-2.5-pro` `in=1/out=0`), so a zero-check missed every
tau2 and appworld case. Any leg counted above has **understated** token totals — its pass rate
remains valid. See `docs/exgentic-agent-bug-report-20260901.md`.

One more, and it bears on the *output* token and latency columns rather than the `llm` one:
**each LLM gateway caches completions**, keyed on the request body, so a leg that repeats an
earlier leg's task on the same model can be handed back the stored response — same response
`id`, same `usage`. Task selection is deterministic, so within one side the legs sharing a
benchmark do repeat tasks; each side's own report names them. That makes per-call latency and
output tokens non-independent **within** a side. **Both sides used the same gateway, so they share one cache** — but the later matrix started 0.4 h after the earlier one finished, past the ~10 min TTL, so neither side can have replayed the other: a figure the two agree on is still two measurements, not one counted twice. Latency is not a
hit detector either — a measured replay took 3.0 s, the same as a miss.

## Reproducing this report

```sh
python3 reference/gen-12run-comparison.py \
  --gateway same /tmp/autobench/run12-ykt3-v136-20261012.json "OpenShift (ykt3, single-cluster)" /tmp/autobench/run12-kind-v136-20261012.json "KinD (single-node local)" v1.36 docs/results/v1.36-2026-10-12/12run-comparison.md
```

Both run JSONs and the mirrored artifacts they point at come from `reference/run-12.py`. If
`/tmp` has been pruned since, re-hydrate with `reference/remirror.py` first — a missing
artifact is treated as an empty one and yields a quietly shorter report.
