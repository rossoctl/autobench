# 12-Run Comparison — OCP (ykt3→ykt2) vs KinD, both Service v1.27

**Report generated:** 2026-09-12T19:13:12Z

**Platforms compared:** OCP (ykt3→ykt2) vs KinD

Both sides ran the same 12 request bodies, the same Service version, and verified-identical
instance config. Every leg deploys fresh. Task selection is deterministic, so the same
`task_id` is the same task on both platforms — differences are attributable to the platform.

All numbers are derived from the mirrored `report.ndjson` artifacts.

**Contents**

- [S3 artifact locations](#s3-artifact-locations)
- [Pass rate, wall time, tokens](#pass-rate-wall-time-tokens)
- [Token distribution per task](#token-distribution-per-task)
  - [Input tokens](#input-tokens)
  - [Output tokens](#output-tokens)
- [Totals](#totals)
- [What matches](#what-matches)
- [Where they differ](#where-they-differ)
- [Caveats on the `llm` column and on token totals](#caveats-on-the-llm-column-and-on-token-totals)

## S3 artifact locations

Every number below is derived from artifacts published to S3. Both sides share one bucket and differ only in the key prefix (the instance's configured `s3.prefix` and encoded issuer host).

| | |
|---|---|
| **URL root** | `https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com/` |
| **OCP (ykt3→ykt2) key prefix** (96 objects) | `ykt3-to-ykt2/benchmarker/keycloak-keycloak.apps.ykt2.hcp.res.ibm.com-realms-rossoctl/` |
| **KinD key prefix** (96 objects) | `kind/benchmarker/keycloak.localtest.me-8080-realms-rossoctl/` |

Key layout is `<s3.prefix>/<caller>/<iss-host-encoded>/<benchmark>/<run_id>/<artifact>`, so **benchmark selects cleanly by prefix** but **the model in use does not appear in the key at all** — gsm8k mixes models across its runs, so no prefix separates them. Resolve model -> `run_id` from the pass-rate table below when you need per-model objects.

| benchmark | objects OCP (ykt3→ykt2) | objects KinD | sub-prefix |
|---|---:|---:|---|
| appworld | 16 | 16 | `appworld/` |
| gsm8k | 64 | 64 | `gsm8k/` |
| tau2 | 16 | 16 | `tau2/` |

Objects are readable **and listable anonymously**, so treat anything written here as public. What is exposed: the **keys** carry the caller's username and the Keycloak issuer host, and `run.json` / `report.ndjson` can carry **exception strings** from failed tasks. No artifact contains task prompts or model outputs — the record schema is ids, counts and durations, and `span_report.*` publishes only a fixed whitelist of structural/numeric span fields.

## Pass rate, wall time, tokens

| # | bench | p | pass OCP (ykt3→ykt2) | pass KinD | Δ | wall OCP (ykt3→ykt2) | wall KinD | KinD/OCP (ykt3→ykt2) | in OCP (ykt3→ykt2) | in KinD | in Δ |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | gsm8k | 1 | 1.0 | 1.0 | +0.00 | 14 | 6 | 0.43x | 320 | 320 | same |
| 2 | gsm8k | 1 | 1.0 | 1.0 | +0.00 | 60 | 55 | 0.91x | 3166 | 3166 | same |
| 3 | gsm8k | 4 | 1.0 | 1.0 | +0.00 | 166 | 72 | 0.43x | 15684 | 15684 | same |
| 4 | gsm8k | 4 | 0.8 | 1.0 | +0.20 | 13 | 20 | 1.55x | 3927 | 4117 | +190 |
| 5 | gsm8k | 4 | 1.0 | 1.0 | +0.00 | 29 | 3 | 0.11x | 1564 | 1564 | same |
| 6 | gsm8k | 4 | 1.0 | 1.0 | +0.00 | 31 | 6 | 0.19x | 1564 | 1564 | same |
| 7 | gsm8k | 4 | 1.0 | 1.0 | +0.00 | 25 | 8 | 0.34x | 1564 | 1564 | same |
| 8 | gsm8k | 4 | 1.0 | 1.0 | +0.00 | 29 | 8 | 0.29x | 1564 | 1564 | same |
| 9 | tau2 | 1 | 0.9 | 0.8 | -0.10 | 1184 | 920 | 0.78x | 854508 | 948122 | +93614 |
| 10 | tau2 | 4 | 0.8 | 0.8 | +0.00 | 725 | 458 | 0.63x | 1484987 | 1561308 | +76321 |
| 11 | appworld | 1 | 0.0 | 0.0 | +0.00 | 2211 | 1530 | 0.69x | 1897947 | 659907 | -1238040 |
| 12 | appworld | 4 | 0.0 | 0.0 | +0.00 | 1887 | 2075 | 1.10x | 5162895 | 6444789 | +1281894 |

## Token distribution per task

For each direction: `median` (robust centre), then `mean`, then `CV` (population sigma / mean — dimensionless, so spread is comparable across benchmarks whose token counts differ by orders of magnitude). A mean well above the median means right-skew: a few long tasks dominate. CV shares its denominator with `mean`, not `median`, and is `—` for single-task runs.

**Which direction varies more is benchmark-dependent, and output usually wins** — measured at OUT CV > IN CV in 27 of 33 legs across these matrices. On single-turn gsm8k the prompt is nearly constant (IN CV ~0.06-0.09) while answer length swings with how much the model reasons (OUT CV 0.4-1.0). Only long-horizon **appworld** inverts it (IN CV 0.30-0.93 above OUT), because there the tasks differ enormously in turn count and every call re-sends the whole conversation, so compounding context dominates. tau2 sits between, marginally output-led.

### Input tokens

| # | bench | median OCP (ykt3→ykt2) | mean OCP (ykt3→ykt2) | CV OCP (ykt3→ykt2) | median KinD | mean KinD | CV KinD |
|---|---|---:|---:|---:|---:|---:|---:|
| 1 | gsm8k | 320 | 320 | — | 320 | 320 | — |
| 2 | gsm8k | 312 | 317 | 0.08 | 312 | 317 | 0.08 |
| 3 | gsm8k | 310 | 314 | 0.06 | 310 | 314 | 0.06 |
| 4 | gsm8k | 815 | 785 | 0.37 | 815 | 823 | 0.45 |
| 5 | gsm8k | 306 | 313 | 0.09 | 306 | 313 | 0.09 |
| 6 | gsm8k | 306 | 313 | 0.09 | 306 | 313 | 0.09 |
| 7 | gsm8k | 306 | 313 | 0.09 | 306 | 313 | 0.09 |
| 8 | gsm8k | 306 | 313 | 0.09 | 306 | 313 | 0.09 |
| 9 | tau2 | 87870 | 85451 | 0.11 | 97934 | 94812 | 0.09 |
| 10 | tau2 | 79958 | 78157 | 0.21 | 82517 | 78065 | 0.25 |
| 11 | appworld | 398663 | 379589 | 0.26 | 171212 | 164977 | 0.38 |
| 12 | appworld | 211040 | 286828 | 0.65 | 388535 | 379105 | 0.64 |

### Output tokens

| # | bench | median OCP (ykt3→ykt2) | mean OCP (ykt3→ykt2) | CV OCP (ykt3→ykt2) | median KinD | mean KinD | CV KinD |
|---|---|---:|---:|---:|---:|---:|---:|
| 1 | gsm8k | 87 | 87 | — | 87 | 87 | — |
| 2 | gsm8k | 151 | 215 | 0.96 | 87 | 183 | 0.70 |
| 3 | gsm8k | 151 | 195 | 0.82 | 120 | 189 | 0.77 |
| 4 | gsm8k | 54 | 65 | 0.40 | 54 | 56 | 0.40 |
| 5 | gsm8k | 87 | 138 | 0.74 | 87 | 126 | 0.61 |
| 6 | gsm8k | 87 | 138 | 0.54 | 87 | 126 | 0.61 |
| 7 | gsm8k | 87 | 138 | 0.54 | 87 | 126 | 0.89 |
| 8 | gsm8k | 87 | 138 | 0.54 | 87 | 164 | 0.76 |
| 9 | tau2 | 2230 | 2176 | 0.15 | 2350 | 2371 | 0.11 |
| 10 | tau2 | 1920 | 1875 | 0.26 | 1720 | 1862 | 0.33 |
| 11 | appworld | 34569 | 33803 | 0.16 | 19540 | 19209 | 0.27 |
| 12 | appworld | 25065 | 26187 | 0.49 | 40297 | 32594 | 0.55 |

## Totals

- input tokens: OCP (ykt3→ykt2) 9,429,690 · KinD 9,643,669
- output tokens: OCP (ykt3→ykt2) 712,829 · KinD 706,254
- wall: OCP (ykt3→ykt2) 6373s · KinD 5161s
- rows with lost token attribution: OCP (ykt3→ykt2) 0/138 · KinD 0/137  — **token capture complete on both sides**

## What matches

- **10 of 12 pass rates identical**: #1, #2, #3, #5, #6, #7, #8, #10, #11, #12.
- **7 runs have byte-identical input-token totals**: #1, #2, #3, #5, #6, #7, #8 — deterministic task selection plus the same model
  means identical work, the strongest available check that this is a like-for-like comparison
  rather than merely a similar one.

## Where they differ

- **2 run(s) differ**: #4 (+0.20), #9 (-0.10).
- Interpret small deltas on small runs with care: one task on a 5-task run moves pass_rate by
  0.20. tau2 and appworld are additionally nondeterministic per episode.

## Caveats on the `llm` column and on token totals

Each task issues an extra `max_tokens=1` probe call that is counted as a `chat` span, so
LLM-call counts read one high per task.

Whether that probe *succeeds* is model-dependent, and it determines how a lost-span row looks:
a reasoning model (`gpt-5-mini`) has the probe rejected and records no usage (`in=out=0`),
while `claude-sonnet-5` / `gemini-2.5-pro` accept it and record the probe's own usage
(`in=8/out=1`, `in=1/out=0`). So a `tokens == 0` check silently misses the latter two; the
detector used above is structural instead (`llm<=1` with `tool>=2` is impossible).
Any leg counted above has **understated** token totals — pass rates remain valid.
See `docs/exgentic-agent-bug-report-20260901.md`.
