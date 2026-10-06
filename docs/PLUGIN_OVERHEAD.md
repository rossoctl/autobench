# What AuthBridge plugins cost, and how to find out for your cluster

This document is for someone who has just been handed a benchmark report and asked "so how much does
turning on authorization cost us?" The short answer: on both clusters we measured, almost all of it is
**the IBAC judge** — about a second and a half per task — and the other layers are too small to rank.
Why that answer can be trusted, and where it stops, is the rest of this page. If you want the raw
reports instead, they are in [docs/results/](results/README.md).

<!-- toc -->

**Contents**

- [The vocabulary first](#the-vocabulary-first)
  - [One piece of syntax that reads backwards](#one-piece-of-syntax-that-reads-backwards)
- [The headline: the judge is the cost, on both clusters](#the-headline-the-judge-is-the-cost-on-both-clusters)
  - [One precondition that is easy to skip and invalidates everything](#one-precondition-that-is-easy-to-skip-and-invalidates-everything)
  - [It is not a timeout — we checked](#it-is-not-a-timeout--we-checked)
- [What else travels between clusters](#what-else-travels-between-clusters)
- [Two traps in reading any of our latency numbers](#two-traps-in-reading-any-of-our-latency-numbers)
  - [Warm-up is one concurrency wave, not "the first few tasks"](#warm-up-is-one-concurrency-wave-not-the-first-few-tasks)
  - [The deploy is the unit of replication, not the task](#the-deploy-is-the-unit-of-replication-not-the-task)
- [Recommendations](#recommendations)
- [What is still unmeasured](#what-is-still-unmeasured)
- [Reproducing](#reproducing)

<!-- /toc -->

## The vocabulary first

Three things get conflated in casual conversation, and the whole result depends on keeping them
apart.

**The sidecar** is a proxy container injected next to the agent pod when AuthBridge is enabled. All
of the agent's outbound tool traffic goes through it. Its cost is paid whether or not any policy
actually does anything.

**A plugin** is a policy that runs inside that sidecar. We use two: `auth`, which checks the caller's
token, and `ibac`, which asks an external **judge** whether a specific tool call is authorized.

**The judge** is itself an LLM call. This matters more than it sounds: it means IBAC's cost inherits
inference latency and inference *variance*, which is where both the cost and the long tail of a
judged task come from.

A **preset** bundles these. The four configurations we measure:

| preset | sidecar | `auth` | `ibac` |
|---|---|---|---|
| `baseline` (AuthBridge off) | no | — | — |
| `auth-only` | yes | yes | no |
| `ibac-only` | yes | no | yes |
| `full` | yes | yes | yes |

The conditions are **nested** on purpose — each adds exactly one layer to the one above it — so the
difference between two adjacent rows is the marginal cost of that layer, and nothing else.

### One piece of syntax that reads backwards

`--plugin ibac:observe` renders in the pipeline as `on_error: observe`. It is a **failure-mode**
policy: it says what to do when the judge call *fails*, not "run the judge in observe-only mode." On
a healthy run, therefore, `full` and `full+ibac:observe` are the same configuration — and indeed we
measured them as indistinguishable on both clusters. That agreement is a useful sanity check on the
method rather than a finding about `observe`.

## The headline: the judge is the cost, on both clusters

We ran the identical 13-leg experiment on two single-cluster installs of AutoBench v1.35 —
OpenShift (`ykt3`) and a single-node KinD cluster — with the same 50 tasks in the same
deterministic order, the same model and one LLM gateway, one platform after the other. Steady-state
non-LLM time per task:

| condition | OpenShift median | KinD median | OpenShift step | KinD step |
|---|---:|---:|---:|---:|
| `baseline` | 0.168 s | 0.097 s | — | — |
| `auth-only` | 0.214 s | 0.130 s | +0.05 | +0.03 |
| `ibac-only` | 1.569 s | 1.610 s | **+1.36** | **+1.48** |
| `full` | 1.584 s | 1.602 s | +0.01 | −0.01 |
| `full+ibac:observe` | 1.583 s | 1.607 s | −0.00 | +0.00 |

Read the two right-hand columns against each other. **Both clusters put the cost in the same place**:
the step from `auth-only` to `ibac-only`, which is the judge call. The sidecar's mere presence costs a
few hundredths of a second; adding `auth` on top of IBAC (`full`) and switching the failure policy
(`observe`) cost nothing measurable. With the judge engaged, the same condition lands within a few
percent on the two clusters (1.57 s and 1.61 s).

That agreement is a measurement, not an assumption, and it is the reason the cross-platform report
checks attribution rather than presuming it: where the cost lands depends on what the intercepted
path does on each cluster, and that is not guaranteed to travel. So the practical rule survives the
agreement: **name the cluster beside any per-task plugin figure.** Here the seconds are close, not
equal.

### One precondition that is easy to skip and invalidates everything

Every gsm8k leg in this design sends the *same* 50 prompts to the same model — that is what makes the
conditions comparable — and the LLM gateway caches completions keyed on the request body for a
measured TTL of about ten minutes. Run the legs back to back and the later ones are served the
earlier ones' completions. For a study whose outcome *is* latency, that is not noise; it is the
measurement disappearing, and it disappears **in run order**, which is the same shape as a plugin
effect.

There is a cheap check for it that needs no statistics, and it is worth applying to any latency
report of a nested design. The conditions **nest** — every non-baseline condition is baseline *plus*
the sidecar — so no leg carrying the sidecar can be faster than a leg without one. A replayed leg
breaks that ordering outright, because it skips work the leg it nests above actually did. Both
per-platform reports compute the check and print the verdict rather than arguing it; on the runs
reported here the fastest sidecar-carrying leg sits 1.3x above the fastest baseline leg on both
clusters, so the ordering holds.

Spacing the legs (`BM_CACHE_GAP=900`, see [Reproducing](#reproducing)) is what buys that, and it is
cheaper than it sounds: only the shortfall is slept, and interleaving the two tau2 legs into the
gsm8k sequence lets real work absorb two of the gaps.

### It is not a timeout — we checked

A step of about a second and a half per task invites an obvious guess: a timeout or a failing retry
somewhere in the sidecar's egress path. A timeout leaves a signature — values piled on a round
number with a small spread. The judged distributions have none: `full` runs **0.21–9.63 s** around a
median of 1.58 s on OpenShift (SD 1.65 s), and **0.12–7.75 s** around 1.60 s on KinD (SD 1.28 s),
with no 0.1 s bin holding more than 12% of tasks. Broad, with nothing on a round number — variable
latency, not a fixed penalty.

The tail has an owner: the judge is an LLM call, so judged tasks inherit inference variance. Two
consequences follow. The cost should move with the judge's model and gateway rather than with the
cluster, which is consistent with it landing in the same layer on both. And **engaging the judge
costs reproducibility, not only latency** — the spread grows with the median, which matters if you
are using these benchmarks to detect regressions.

## What else travels between clusters

**Every serial tool call is authorized.** With `max_parallel_sessions=1`, no two calls can race for
one cache entry. Both clusters returned 10 tasks attempted, 10 serial tool calls, 10 judge calls — a
ratio of exactly 1.00 each. A judge cache with a time-to-live would have collapsed ten same-shape
serial calls to roughly one judge call on *either* cluster; it did not, twice, independently.
**TTL decision caching is ruled out.**

That leaves an open item worth naming, because it has a security consequence. At `p=4` the judge
count falls slightly short of the tool count. Two mechanisms explain that equally well — benign
single-flight deduplication, or fail-open under race — and they produce *identical counts*.
Distinguishing them is a code-reading task, not a measurement task. It is the one thing this study
could not settle.

**The judged-call ratio is ~1.** For every IBAC preset it lands at 0.94–1.02 on both clusters; for
`baseline` and `auth-only` it is exactly 0.00, which is correct and also confirms nothing else is
consulting the judge. This is worth knowing because the ratio looks alarming on small runs: a 5-task
leg can easily show "3 of 5 calls authorized," which invites the conclusion that the judge is being
skipped. At n=50 that reading dissolves. **Don't read a judged/tool ratio off a handful of calls.**

**Per-call cost is not a cross-benchmark constant.** There is an obvious shortcut for estimating what
a plugin will cost on tau2 or appworld without running them: take the per-tool-call delta measured on
cheap gsm8k legs and multiply by the target benchmark's tool-call count. tau2 makes ~11 tool calls
per task against gsm8k's ~1, so it tests the assumption directly:

| cluster | tau2 measured Δ/task | projected from gsm8k | measured/projected |
|---|---:|---:|---:|
| OpenShift | +28.3 s | 17.0 s | 1.66x |
| KinD | +33.4 s | 16.6 s | 2.01x |

The shortcut gets the order of magnitude right and **under-estimates on both clusters, by 1.7–2.0x**.
A per-call figure is not the constant the arithmetic needs; a tool-call-heavy benchmark pays more per
call than gsm8k does. Measuring a benchmark's plugin cost directly costs two legs; do that instead,
or apply at least the factor of two.

## Two traps in reading any of our latency numbers

These generalize past the plugin question, and they are the reason the designed study exists at all.

### Warm-up is one concurrency wave, not "the first few tasks"

Across the *no-sidecar* baseline legs, splitting the tasks at the first concurrency wave (the first
`num_parallel` tasks) isolates a warm-up penalty: **3.68x and 8.51x** on OpenShift, **5.51x and
10.65x** on KinD, where a ~0.1 s steady state makes the same fixed startup cost a larger multiple.
Splitting at a fixed cutoff of 10 tasks instead gives 0.95x and 1.12x on OpenShift and 1.17x and
**0.88x** on KinD: most of the effect is buried by mixing steady-state tasks into the "warm" bucket,
and two legs drop below 1 — reporting warm-up as a *speed-up*. The wave is the real boundary; a fixed
task count is not, and our analyzers derive the cutoff per leg from the artifacts' own `num_parallel`.

The consequence for small runs is severe. At `p=4`, a 5-task leg spends *four of its five tasks*
inside the warm-up transient. A per-task average from such a leg is mostly measuring startup.

### The deploy is the unit of replication, not the task

Every task in a leg shares one deployment. So a per-task confidence interval answers "how variable
are tasks within this one deploy?" — not "how variable is this configuration?" The honest noise
floor is the spread between two independent deploys of the *same* condition: typically **0.02 s** on
OpenShift and **0.12 s** on KinD, so a step has to exceed about **0.07 s** and **0.39 s**
respectively (three between-deploy SDs) to count as resolved.

On both clusters only the judge's step clears that floor. So with two deploys per condition, a
*ranking* of the other presets against each other is unavailable at any task count. Adding tasks
tightens the wrong interval. If you need the ranking, add deploys: roughly `16 · σ² / δ²` deploys per
condition for 80% power at effect size `δ`.

That formula cuts the other way too, and it is the useful half. With these floors, **the two deploys
already run are enough to resolve a 1 s effect on both clusters** — so the sub-second steps are not
under-replicated, they are genuinely smaller than a second. Resolving a 0.1 s step would need a
couple of deploys per condition on OpenShift but ~27 on KinD, where the floor is wider.

KinD's replicates also disagree for four of the five conditions, by the per-task test — each pair of
deploys reads as two populations. That widens the honest interval on KinD rather than biasing it,
and it is part of why its floor is the wider of the two.

## Recommendations

1. **Measure on the cluster whose numbers you intend to quote.** Use
   `reference/plugin_study_specs.json` unchanged so the design (nesting, crossover, n=50) is
   preserved, and **space the legs past the gateway's cache** — an unspaced execution measures the
   cache, not the plugins. Two runs, one per platform, one at a time.
2. **Quote the structural findings freely** — serial calls are all authorized, TTL caching is ruled
   out, the judged ratio is ~1, the cost sits in the judge, per-call cost is not a cross-benchmark
   constant. These reproduced on both clusters.
3. **Name the cluster beside any absolute per-task second figure.** The two clusters agree closely
   here; that is a measured fact about these two, not a law.
4. **Do not rank presets** from a two-deploy study. Say "the dominant layer is the judge and
   everything else is below the noise floor" — and you can add "and below one second," which is a
   budgeting answer rather than a shrug.
5. **Budget for the judge's LLM call.** On these clusters it is ~1.4–1.5 s per task, with a tail of
   several seconds. The other layers are rounding error.
6. **If you need per-benchmark plugin cost, measure that benchmark.** Two legs.

## What is still unmeasured

- **Cold start and admission.** `agent_call_s` excludes deploy and readiness time entirely, so the
  cost of *injecting* the sidecar is invisible. It needs deploy-duration instrumentation.
- **Infrastructure cost.** Every `mcp_*`/`a2a_*` CPU and memory field is `0.0` and `has_infra` is
  `false`, because those values come from an `infra` attribute on the agent's OTEL root span that
  the upstream agent does not emit. No number of runs fixes this.
- **Whether the concurrency shortfall is fail-open.** See above; a code-reading task.
- **The LLM path,** by construction. The metric is `agent_call_s − llm_total_s`, which strips shared
  gateway variance. If a plugin changed LLM latency or token counts, this would not show it — which
  is safe only because the identical-work check is verified in each report rather than assumed.
- **KinD's judge step includes DNS retries.** The KinD node's resolver fails a lookup now and then,
  and the judge proxy retries it (only when the request never left the pod). In the KinD study those
  retries slept 103 s across 395 verdicts — about **0.26 s per judged call on average** — so part of
  KinD's +1.48 s is the node's DNS rather than the judge. OpenShift's judge made no retries.

## Reproducing

One platform at a time. Both platforms here called one LLM gateway, and even on separate gateways the
same upstream model providers sit behind them, so parallel runs contend where it matters and the
latency numbers stop meaning anything.

`BM_CACHE_GAP` is not optional here. Every gsm8k leg in this design sends the same 50 prompts to the
same model, so without it each leg replays the previous one's completions out of the gateway's cache
(TTL ~10 min) — which removes the model call from the very quantity being measured, in run order, and
mimics exactly the kind of per-condition effect the study is looking for. `BM_ORDER` interleaves the
two tau2 legs into the gsm8k sequence so two of the gaps are absorbed by real work rather than slept.

```sh
BM_SPECS=reference/plugin_study_specs.json BM_LABEL=pstudy-<platform> \
  BM_CACHE_GAP=900 BM_ORDER=111,112,101,102,103,104,105,113,106,107,108,109,110 \
  python3 reference/run-12.py        # ~3–3.5 h, most of it the cache gaps; detach it (CLAUDE.md)

python3 reference/gen-plugin-study.py <run.json> reference/plugin_study_specs.json \
  v1.35 '<platform label>' out.md <judge.ts>

python3 reference/gen-plugin-study-xplat.py reference/plugin_study_specs.json v1.35 xplat.md \
  '<label A>' <runA.json> <judgeA.ts> -- '<label B>' <runB.json> <judgeB.ts>
```

`judge.ts` is one ISO-8601 timestamp per judge **verdict**: the judge Deployment's log with
`--timestamps`, keeping only the `-> 200` lines (the proxy also logs its retries, which are not
verdicts).

Both generators check the spacing themselves and print the ⚠ / ✅ verdict into the report, so an
unspaced execution cannot be published as if it were clean.

The cross-platform generator deliberately does **not** pool the two clusters into a single interval.
Pooling would assume the platforms are interchangeable; that is a claim to measure each time, not to
assume.
