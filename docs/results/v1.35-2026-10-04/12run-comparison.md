# 12-Run Comparison — OpenShift (ykt3, single-cluster) vs KinD (single-node local), both Service v1.35

**Report generated:** 2026-10-05T01:19:49Z

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
| 1 | gsm8k | 1 | 1.0 | 1.0 | +0.00 | 4 | 2 | 0.70x | 320 | 320 | same |
| 2 | gsm8k | 1 | 1.0 | 1.0 | +0.00 | 29 | 27 | 0.93x | 3166 | 3166 | same |
| 3 | gsm8k | 4 | 1.0 | 1.0 | +0.00 | 34 | 33 | 0.96x | 15684 | 15684 | same |
| 4 | gsm8k | 4 | 0.8 | 0.8 | +0.00 | 9 | 8 | 0.96x | 3908 | 4177 | +269 |
| 5 | gsm8k | 4 | 1.0 | 1.0 | +0.00 | 6 | 6 | 0.91x | 1564 | 1564 | same |
| 6 | gsm8k | 4 | 1.0 | 0.8 | -0.20 | 6 | 5 | 0.82x | 1564 | 1564 | same |
| 7 | gsm8k | 4 | 0.8 | 1.0 | +0.20 | 9 | 6 | 0.62x | 1564 | 1564 | same |
| 8 | gsm8k | 4 | 1.0 | 1.0 | +0.00 | 7 | 9 | 1.21x | 1564 | 1564 | same |
| 9 | tau2 | 1 | 0.9 | 1.0 | +0.10 | 511 | 500 | 0.98x | 936612 | 916576 | -20036 |
| 10 | tau2 | 4 | 0.9 | 0.85 | -0.05 | 282 | 266 | 0.94x | 1695959 | 1726957 | +30998 |
| 11 | appworld | 1 | 0.0 | 0.0 | +0.00 | 1284 | 1445 | 1.13x | 1420531 | 1564276 | +143745 |
| 12 | appworld | 4 | 0.0 | 0.0 | +0.00 | 1402 | 1484 | 1.06x | 5949228 | 5349900 | -599328 |

## Why tasks failed

Every task that ended with an error, bucketed by what kind of thing went wrong. This is the
context a pass-rate delta needs: a task lost to a socket closing and a task lost to the
model answering wrongly sit in the same `evaluated_pass` denominator and are
indistinguishable in every table above, but only one of them is a statement about the
agent. Counted from `run.json`, which carries one result per task unconditionally — a task
killed by a per-task timeout leaves no `report.ndjson` row at all.

| cause | OpenShift (ykt3, single-cluster) | KinD (single-node local) | what it means |
|---|---:|---:|---|
| per-task timeout | 0 | 1 | The task exceeded its `task_timeout_seconds`. On appworld this is the dominant failure mode and is an upstream agent behaviour, not a resource limit — see the appworld notes in the per-platform reports. |
| agent (upstream defect) | 4 | 4 | The agent failed on its own — a malformed or empty completion, or its cloudpickle `_asyncio.Task` race. An upstream defect; the task never had a chance to be scored. |
| wrong answer | 1 | 1 | The agent ran, answered, and the answer was rejected. **The only bucket that is a genuine statement about the model's ability.** |

**No task on either side was lost to an infrastructure failure** (transport, gateway, judge or MCP), so every pass-rate delta below is attributable to the agent, the model or the benchmark.

## Token distribution per task

For each direction: `median` (robust centre), then `mean`, then `CV` (population sigma / mean — dimensionless, so spread is comparable across benchmarks whose token counts differ by orders of magnitude). A mean well above the median means right-skew: a few long tasks dominate. CV shares its denominator with `mean`, not `median`, and is `—` for single-task runs.

**Which direction varies more is benchmark-dependent, and output usually wins** — measured on *these* matrices at OUT CV > IN CV in **16 of the 22 leg-sides** that ran more than one task (one leg on one platform; a single-task run has no CV). By benchmark, and by model where a benchmark ran more than one: **gsm8k** IN 0.06–0.09 vs OUT 0.55–0.99 on `gpt-5-mini-2025-08-07`, IN 0.38–0.44 vs OUT 0.32–0.45 on `gpt-4.1`; **tau2** IN 0.16–0.21 vs OUT 0.16–0.39; **appworld** IN 0.57–0.88 vs OUT 0.40–0.62. The mechanism is visible in the gsm8k rows: a model that answers in one call re-sends a nearly constant prompt, so only its answer length swings, while a model that needs tool round-trips varies on the input side too — same five tasks, different shape. Only long-horizon **appworld** is input-led in every one of its leg-sides: there the tasks differ enormously in turn count and every call re-sends the whole conversation, so compounding context dominates the input side.

### Input tokens

| # | bench | median OpenShift (ykt3, single-cluster) | mean OpenShift (ykt3, single-cluster) | CV OpenShift (ykt3, single-cluster) | median KinD (single-node local) | mean KinD (single-node local) | CV KinD (single-node local) |
|---|---|---:|---:|---:|---:|---:|---:|
| 1 | gsm8k | 320 | 320 | — | 320 | 320 | — |
| 2 | gsm8k | 312 | 317 | 0.08 | 312 | 317 | 0.08 |
| 3 | gsm8k | 310 | 314 | 0.06 | 310 | 314 | 0.06 |
| 4 | gsm8k | 807 | 782 | 0.38 | 807 | 835 | 0.44 |
| 5 | gsm8k | 306 | 313 | 0.09 | 306 | 313 | 0.09 |
| 6 | gsm8k | 306 | 313 | 0.09 | 306 | 313 | 0.09 |
| 7 | gsm8k | 306 | 313 | 0.09 | 306 | 313 | 0.09 |
| 8 | gsm8k | 306 | 313 | 0.09 | 306 | 313 | 0.09 |
| 9 | tau2 | 93544 | 93661 | 0.16 | 93134 | 91658 | 0.16 |
| 10 | tau2 | 86490 | 84798 | 0.20 | 90514 | 86348 | 0.21 |
| 11 | appworld | 214328 | 284106 | 0.57 | 153615 | 312855 | 0.69 |
| 12 | appworld | 206296 | 297461 | 0.88 | 223512 | 281574 | 0.67 |

### Output tokens

| # | bench | median OpenShift (ykt3, single-cluster) | mean OpenShift (ykt3, single-cluster) | CV OpenShift (ykt3, single-cluster) | median KinD (single-node local) | mean KinD (single-node local) | CV KinD (single-node local) |
|---|---|---:|---:|---:|---:|---:|---:|
| 1 | gsm8k | 86 | 86 | — | 86 | 86 | — |
| 2 | gsm8k | 150 | 195 | 0.61 | 86 | 188 | 0.98 |
| 3 | gsm8k | 86 | 160 | 0.76 | 150 | 179 | 0.74 |
| 4 | gsm8k | 50 | 63 | 0.45 | 62 | 63 | 0.32 |
| 5 | gsm8k | 86 | 214 | 0.76 | 86 | 201 | 0.99 |
| 6 | gsm8k | 86 | 125 | 0.62 | 86 | 137 | 0.55 |
| 7 | gsm8k | 86 | 189 | 0.70 | 279 | 278 | 0.62 |
| 8 | gsm8k | 86 | 150 | 0.66 | 86 | 240 | 0.93 |
| 9 | tau2 | 2275 | 2253 | 0.16 | 2302 | 2233 | 0.19 |
| 10 | tau2 | 1832 | 1992 | 0.39 | 1932 | 1977 | 0.31 |
| 11 | appworld | 22409 | 26474 | 0.40 | 17131 | 28792 | 0.57 |
| 12 | appworld | 20792 | 25961 | 0.62 | 24835 | 27632 | 0.52 |

## Tool-selection calls

Not every LLM call works on the task. The agent image defaults to `enable_tool_shortlisting = True, max_selected_tools = 30`: above that many advertised tools, each turn opens with an extra call that carries the whole tool inventory and asks the model to rank it, and only the winners' schemas reach the call that acts. Both kinds are inside the `llm` counts and the token totals above, so the share is worth knowing before reading either — and because it is a property of the tool surface rather than of the cluster, the two sides are a check on each other.

| bench | calls/task OpenShift (ykt3, single-cluster) | select OpenShift (ykt3, single-cluster) | IN% OpenShift (ykt3, single-cluster) | calls/task KinD (single-node local) | select KinD (single-node local) | IN% KinD (single-node local) |
|---|---:|---:|---:|---:|---:|---:|
| gsm8k | 1.1 | 0.0 (0%) | 0% | 1.1 | 0.0 (0%) | 0% |
| tau2 | 11.0 | 0.0 (0%) | 0% | 11.2 | 0.1 (1%) | 0% |
| appworld | 30.3 | 15.2 (50%) | 67% | 30.1 | 15.1 (50%) | 67% |

`gsm8k` stay at **zero** on both sides — they advertise fewer tools than the threshold, so shortlisting cannot fire, which is the control that keeps ordinary multi-turn traffic from being counted as selection. `appworld` spends **half its calls** selecting on both platforms, and the input-token share agrees to within a few points (67% on OpenShift (ykt3, single-cluster) vs 67% on KinD (single-node local)). So the selection overhead is a property of the tool surface, not of the cluster: it inflates both sides' appworld token totals equally and does not bias the comparison — but it does mean a per-task token or dollar figure for that benchmark is mostly the price of choosing tools.

## Totals

- input tokens: OpenShift (ykt3, single-cluster) 10,031,664 · KinD (single-node local) 9,587,312
- output tokens: OpenShift (ykt3, single-cluster) 727,716 · KinD (single-node local) 746,363
- wall: OpenShift (ykt3, single-cluster) 3583s · KinD (single-node local) 3789s
- rows with lost token attribution: OpenShift (ykt3, single-cluster) 0/141 · KinD (single-node local) 0/140  — **token capture complete on both sides**
- tasks lost to the health probe: OpenShift (ykt3, single-cluster) 0/141 · KinD (single-node local) 0/141  — **every task reached the model on both sides**

## What matches

- **8 of 12 pass rates identical**: #1, #12, #2, #3, #11, #5, #4, #8.
- **7 runs have byte-identical input-token totals**: #1, #2, #3, #5, #6, #7, #8 — deterministic task selection plus the same model
  means identical work, the strongest available check that this is a like-for-like comparison
  rather than merely a similar one.

## Where they differ

- **4 of 12 legs differ**: #10 (-0.05), #9 (+0.10), #6 (-0.20), #7 (+0.20).
- Interpret small deltas on small runs with care: one task on a 5-task run moves pass_rate by 0.20. tau2 and appworld are additionally nondeterministic per episode.

## Caveats on the `llm` column and on token totals

**The `llm` column counts real LLM calls one-for-one on both sides, with no probe offset to subtract** — measured: no `chat` span in either matrix carries `max_tokens=1` (1183 and 1155 spans checked). Agents up to `0.3.5.dev131` issued a `max_tokens=1` capability probe that was recorded as a `chat` span, so counts from *those* runs read one high per task; as of `0.3.5.dev145` it is *replaced* by the unbilled `GET /v1/models` check, which emits no span. Do not compare `llm` counts across that boundary: a leg looks like it made one fewer call per task when only the instrumentation changed.

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
output tokens non-independent **within** a side. **Both sides used the same gateway, so they share one cache** — but the later matrix started 0.3 h after the earlier one finished, past the ~10 min TTL, so neither side can have replayed the other: a figure the two agree on is still two measurements, not one counted twice. Latency is not a
hit detector either — a measured replay took 3.0 s, the same as a miss.

## Reproducing this report

```sh
python3 reference/gen-12run-comparison.py \
  --gateway same /tmp/autobench/run12-ykt3-v135-20261004.json "OpenShift (ykt3, single-cluster)" /tmp/autobench/run12-kind-v135-20261004.json "KinD (single-node local)" v1.35 docs/results/v1.35-2026-10-04/12run-comparison.md
```

Both run JSONs and the mirrored artifacts they point at come from `reference/run-12.py`. If
`/tmp` has been pruned since, re-hydrate with `reference/remirror.py` first — a missing
artifact is treated as an empty one and yields a quietly shorter report.
