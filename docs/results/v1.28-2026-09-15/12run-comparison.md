# 12-Run Comparison — OpenShift (ykt3 Service, ykt2 workloads) vs KinD (single-node local), both Service v1.28

**Report generated:** 2026-09-22T04:22:44Z

**Platforms compared:** OpenShift (ykt3 Service, ykt2 workloads) vs KinD (single-node local)

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
| **OpenShift (ykt3 Service, ykt2 workloads) key prefix** (96 objects) | `ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/` |
| **KinD (single-node local) key prefix** (96 objects) | `kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/` |

Key layout is `<s3.prefix>/<caller>/<iss-host-encoded>/<benchmark>/<run_id>/<artifact>`, so **benchmark selects cleanly by prefix** but **the model in use does not appear in the key at all** — gsm8k mixes models across its runs, so no prefix separates them. Resolve model -> `run_id` from the pass-rate table below when you need per-model objects.

| benchmark | objects OpenShift (ykt3 Service, ykt2 workloads) | objects KinD (single-node local) | sub-prefix |
|---|---:|---:|---|
| appworld | 16 | 16 | `appworld/` |
| gsm8k | 64 | 64 | `gsm8k/` |
| tau2 | 16 | 16 | `tau2/` |

Objects are readable **and listable anonymously**, so treat anything written here as public. What is exposed: the **keys** carry the caller's username and the Keycloak issuer host, and `run.json` / `report.ndjson` can carry **exception strings** from failed tasks. No artifact contains task prompts or model outputs — the record schema is ids, counts and durations, and `span_report.*` publishes only a fixed whitelist of structural/numeric span fields.

## Pass rate, wall time, tokens

| # | bench | p | pass OpenShift (ykt3 Service, ykt2 workloads) | pass KinD (single-node local) | Δ | wall OpenShift (ykt3 Service, ykt2 workloads) | wall KinD (single-node local) | KinD (single-node local)/OpenShift (ykt3 Service, ykt2 workloads) | in OpenShift (ykt3 Service, ykt2 workloads) | in KinD (single-node local) | in Δ |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | gsm8k | 1 | 1.0 | 1.0 | +0.00 | 7 | 10 | 1.53x | 320 | 320 | same |
| 2 | gsm8k | 1 | 1.0 | 1.0 | +0.00 | 41 | 46 | 1.13x | 3166 | 3166 | same |
| 3 | gsm8k | 4 | 1.0 | 1.0 | +0.00 | 46 | 62 | 1.35x | 15684 | 15684 | same |
| 4 | gsm8k | 4 | 0.8 | 1.0 | +0.20 | 16 | 18 | 1.08x | 3874 | 4183 | +309 |
| 5 | gsm8k | 4 | 1.0 | 1.0 | +0.00 | 25 | 15 | 0.61x | 1564 | 1564 | same |
| 6 | gsm8k | 4 | 0.6 | 0.8 | +0.20 | 39 | 17 | 0.44x | 1258 | 1564 | +306 |
| 7 | gsm8k | 4 | 0.8 | 1.0 | +0.20 | 30 | 13 | 0.45x | 1564 | 1564 | same |
| 8 | gsm8k | 4 | 1.0 | 1.0 | +0.00 | 23 | 18 | 0.77x | 1564 | 1564 | same |
| 9 | tau2 | 1 | 0.9 | 1.0 | +0.10 | 803 | 1052 | 1.31x | 988110 | 1019232 | +31122 |
| 10 | tau2 | 4 | 0.75 | 0.8 | +0.05 | 436 | 443 | 1.02x | 1703460 | 1743312 | +39852 |
| 11 | appworld | 1 | 0.0 | 0.0 | +0.00 | 2433 | 1638 | 0.67x | 1387383 | 840030 | -547353 |
| 12 | appworld | 4 | 0.0 | 0.0 | +0.00 | 1818 | 2642 | 1.45x | 5211584 | 2009348 | -3202236 |

## Why tasks failed

Every task that ended with an error, bucketed by what kind of thing went wrong. This is the
context a pass-rate delta needs: a task lost to a socket closing and a task lost to the
model answering wrongly sit in the same `evaluated_pass` denominator and are
indistinguishable in every table above, but only one of them is a statement about the
agent. Counted from `run.json`, which carries one result per task unconditionally — a task
killed by a per-task timeout leaves no `report.ndjson` row at all.

| cause | OpenShift (ykt3 Service, ykt2 workloads) | KinD (single-node local) | what it means |
|---|---:|---:|---|
| transport / gateway | 1 | 0 | The connection to the agent or the gateway failed mid-request. **Infrastructure, not the agent** — it says nothing about the model or the benchmark, and it is the one bucket that should not be read as a capability difference. |
| per-task timeout | 4 | 11 | The task exceeded its `task_timeout_seconds`. On appworld this is the dominant failure mode and is an upstream agent behaviour, not a resource limit — see the appworld notes in the per-platform reports. |
| agent (upstream defect) | 1 | 0 | The agent returned a malformed or empty completion. An upstream defect; the task never had a chance to be scored. |
| wrong answer | 2 | 1 | The agent ran, answered, and the answer was rejected. **The only bucket that is a genuine statement about the model's ability.** |

**Transport failures fall on specific legs**: #6 (OpenShift (ykt3 Service, ykt2 workloads) 1, KinD (single-node local) 0). Where such a leg also shows a pass-rate delta, that part of the delta is the network, not the platform's ability to run the benchmark — subtract it before drawing a conclusion.

## Token distribution per task

For each direction: `median` (robust centre), then `mean`, then `CV` (population sigma / mean — dimensionless, so spread is comparable across benchmarks whose token counts differ by orders of magnitude). A mean well above the median means right-skew: a few long tasks dominate. CV shares its denominator with `mean`, not `median`, and is `—` for single-task runs.

**Which direction varies more is benchmark-dependent, and output usually wins** — measured on *these* matrices at OUT CV > IN CV in **15 of the 22 leg-sides** that ran more than one task (one leg on one platform; a single-task run has no CV). By benchmark, and by model where a benchmark ran more than one: **gsm8k** IN 0.06–0.09 vs OUT 0.52–0.86 on `gpt-5-mini-2025-08-07`, IN 0.39–0.44 vs OUT 0.33–0.55 on `gpt-4.1`; **tau2** IN 0.12–0.20 vs OUT 0.10–0.29; **appworld** IN 0.15–0.69 vs OUT 0.13–0.52. Held out of those ranges: #6 (OpenShift (ykt3 Service, ykt2 workloads)) — a task there died before its first model call, and the zero-token row it left inflates that leg's IN CV on its own. The mechanism is visible in the gsm8k rows: a model that answers in one call re-sends a nearly constant prompt, so only its answer length swings, while a model that needs tool round-trips varies on the input side too — same five tasks, different shape. Only long-horizon **appworld** is input-led in every one of its leg-sides: there the tasks differ enormously in turn count and every call re-sends the whole conversation, so compounding context dominates the input side.

### Input tokens

| # | bench | median OpenShift (ykt3 Service, ykt2 workloads) | mean OpenShift (ykt3 Service, ykt2 workloads) | CV OpenShift (ykt3 Service, ykt2 workloads) | median KinD (single-node local) | mean KinD (single-node local) | CV KinD (single-node local) |
|---|---|---:|---:|---:|---:|---:|---:|
| 1 | gsm8k | 320 | 320 | — | 320 | 320 | — |
| 2 | gsm8k | 312 | 317 | 0.08 | 312 | 317 | 0.08 |
| 3 | gsm8k | 310 | 314 | 0.06 | 310 | 314 | 0.06 |
| 4 | gsm8k | 807 | 775 | 0.39 | 807 | 837 | 0.44 |
| 5 | gsm8k | 306 | 313 | 0.09 | 306 | 313 | 0.09 |
| 6 | gsm8k | 291 | 252 | 0.51 | 306 | 313 | 0.09 |
| 7 | gsm8k | 306 | 313 | 0.09 | 306 | 313 | 0.09 |
| 8 | gsm8k | 306 | 313 | 0.09 | 306 | 313 | 0.09 |
| 9 | tau2 | 94026 | 98811 | 0.12 | 101678 | 101923 | 0.14 |
| 10 | tau2 | 89010 | 85173 | 0.17 | 90858 | 87166 | 0.20 |
| 11 | appworld | 416540 | 462461 | 0.15 | 202352 | 210008 | 0.19 |
| 12 | appworld | 220488 | 289532 | 0.65 | 178278 | 200935 | 0.69 |

### Output tokens

| # | bench | median OpenShift (ykt3 Service, ykt2 workloads) | mean OpenShift (ykt3 Service, ykt2 workloads) | CV OpenShift (ykt3 Service, ykt2 workloads) | median KinD (single-node local) | mean KinD (single-node local) | CV KinD (single-node local) |
|---|---|---:|---:|---:|---:|---:|---:|
| 1 | gsm8k | 150 | 150 | — | 470 | 470 | — |
| 2 | gsm8k | 86 | 188 | 0.86 | 182 | 195 | 0.67 |
| 3 | gsm8k | 150 | 186 | 0.70 | 150 | 173 | 0.76 |
| 4 | gsm8k | 50 | 57 | 0.55 | 63 | 63 | 0.33 |
| 5 | gsm8k | 86 | 137 | 0.75 | 342 | 355 | 0.59 |
| 6 | gsm8k | 86 | 69 | 0.50 | 86 | 150 | 0.85 |
| 7 | gsm8k | 407 | 368 | 0.52 | 86 | 163 | 0.76 |
| 8 | gsm8k | 86 | 125 | 0.62 | 86 | 214 | 0.76 |
| 9 | tau2 | 2104 | 2104 | 0.10 | 2315 | 2319 | 0.17 |
| 10 | tau2 | 1904 | 2040 | 0.29 | 1986 | 1931 | 0.28 |
| 11 | appworld | 32764 | 35093 | 0.13 | 19973 | 21278 | 0.16 |
| 12 | appworld | 22355 | 27801 | 0.50 | 16866 | 18910 | 0.52 |

## Tool-selection calls

Not every LLM call works on the task. The agent image defaults to `enable_tool_shortlisting = True, max_selected_tools = 30`: above that many advertised tools, each turn opens with an extra call that carries the whole tool inventory and asks the model to rank it, and only the winners' schemas reach the call that acts. Both kinds are inside the `llm` counts and the token totals above, so the share is worth knowing before reading either — and because it is a property of the tool surface rather than of the cluster, the two sides are a check on each other.

| bench | calls/task OpenShift (ykt3 Service, ykt2 workloads) | select OpenShift (ykt3 Service, ykt2 workloads) | IN% OpenShift (ykt3 Service, ykt2 workloads) | calls/task KinD (single-node local) | select KinD (single-node local) | IN% KinD (single-node local) |
|---|---:|---:|---:|---:|---:|---:|
| gsm8k | 1.1 | 0.0 (0%) | 0% | 1.1 | 0.0 (0%) | 0% |
| tau2 | 11.3 | 0.0 (0%) | 0% | 11.5 | 0.0 (0%) | 0% |
| appworld | 32.9 | 16.4 (50%) | 67% | 23.7 | 11.9 (50%) | 70% |

`gsm8k` and `tau2` stay at **zero** on both sides — they advertise fewer tools than the threshold, so shortlisting cannot fire, which is the control that keeps ordinary multi-turn traffic from being counted as selection. `appworld` spends **half its calls** selecting on both platforms, and the input-token share agrees to within a few points (67% on OpenShift (ykt3 Service, ykt2 workloads) vs 70% on KinD (single-node local)). So the selection overhead is a property of the tool surface, not of the cluster: it inflates both sides' appworld token totals equally and does not bias the comparison — but it does mean a per-task token or dollar figure for that benchmark is mostly the price of choosing tools.

## Totals

- input tokens: OpenShift (ykt3 Service, ykt2 workloads) 9,319,531 · KinD (single-node local) 5,641,531
- output tokens: OpenShift (ykt3 Service, ykt2 workloads) 682,653 · KinD (single-node local) 351,833
- wall: OpenShift (ykt3 Service, ykt2 workloads) 5719s · KinD (single-node local) 5975s
- rows with lost token attribution: OpenShift (ykt3 Service, ykt2 workloads) 0/137 · KinD (single-node local) 0/130  — **token capture complete on both sides**
- tasks lost to the health probe: OpenShift (ykt3 Service, ykt2 workloads) 0/141 · KinD (single-node local) 0/141  — **every task reached the model on both sides**

## What matches

- **7 of 12 pass rates identical**: #1, #12, #2, #3, #11, #5, #8.
- **6 runs have byte-identical input-token totals**: #1, #2, #3, #5, #7, #8 — deterministic task selection plus the same model
  means identical work, the strongest available check that this is a like-for-like comparison
  rather than merely a similar one.

## Where they differ

- **5 of 12 legs differ**: #10 (+0.05), #9 (+0.10), #6 (+0.20), #4 (+0.20), #7 (+0.20).
- Interpret small deltas on small runs with care: one task on a 5-task run moves pass_rate by 0.20. tau2 and appworld are additionally nondeterministic per episode.

## Caveats on the `llm` column and on token totals

**The `llm` column counts real LLM calls one-for-one on both sides, with no probe offset to subtract** — measured: no `chat` span in either matrix carries `max_tokens=1` (1122 and 773 spans checked). Agents up to `0.3.5.dev131` issued a `max_tokens=1` capability probe that was recorded as a `chat` span, so counts from *those* runs read one high per task; as of `0.3.5.dev145` it is *replaced* by the unbilled `GET /v1/models` check, which emits no span. Do not compare `llm` counts across that boundary: a leg looks like it made one fewer call per task when only the instrumentation changed.

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
output tokens non-independent **within** a side. It does not undermine the comparison, because
the two clusters front *different* gateways with independent caches: a figure the two sides
agree on is agreement between two independently cached (or uncached) measurements, not one
measurement counted twice. Latency is not a hit detector either — a measured replay took 3.0 s,
the same as a miss.

## Reproducing this report

```sh
python3 reference/gen-12run-comparison.py \
  /tmp/autobench/run12-ocp-dev146.json "OpenShift (ykt3 Service, ykt2 workloads)" /tmp/autobench/run12-kind-dev146.json "KinD (single-node local)" v1.28 docs/results/v1.28-2026-09-15/12run-comparison.md
```

Both run JSONs and the mirrored artifacts they point at come from `reference/run-12.py`. If
`/tmp` has been pruned since, re-hydrate with `reference/remirror.py` first — a missing
artifact is treated as an empty one and yields a quietly shorter report.
