# What AuthBridge plugins cost, and how to find out for your cluster

This document is for someone who has just been handed a benchmark report and asked "so how much does
turning on authorization cost us?" The short answer is that the question has no single number, and
the reason why is the most useful thing we learned. If you want the raw reports instead, they are in
[docs/results/](results/README.md).

<!-- toc -->

**Contents**

- [The vocabulary first](#the-vocabulary-first)
  - [One piece of syntax that reads backwards](#one-piece-of-syntax-that-reads-backwards)
- [The headline: the two clusters disagree about which layer costs anything](#the-headline-the-two-clusters-disagree-about-which-layer-costs-anything)
  - [It is not a timeout — we checked](#it-is-not-a-timeout--we-checked)
- [What does travel between clusters](#what-does-travel-between-clusters)
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
inference latency and inference *variance*, and it is why the slowest tail in one of our two clusters
belongs to IBAC rather than to the network path.

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
measured them as statistically indistinguishable on both clusters. That agreement is a useful
sanity check on the method rather than a finding about `observe`.

## The headline: the two clusters disagree about which layer costs anything

We ran the identical 13-leg experiment on two clusters — the same Service image, the same 50 tasks
in the same deterministic order, the same model. Steady-state non-LLM time per task:

| condition | OpenShift median | KinD median | OpenShift step | KinD step |
|---|---:|---:|---:|---:|
| `baseline` | 0.649 s | 0.117 s | — | — |
| `auth-only` | 12.424 s | 0.196 s | **+11.77** | +0.08 |
| `ibac-only` | 13.310 s | 2.107 s | +0.89 | **+1.91** |
| `full` | 15.235 s | 2.058 s | +1.92 | −0.05 |

Read the two right-hand columns against each other. On OpenShift the entire expense appears at
`auth-only` — that is, at the *sidecar's mere presence* — and the judge is lost in the noise. On KinD
the sidecar is nearly free and the judge is the whole expense.

**Both readings are correct about their own cluster.** Someone who read only the OpenShift report
would tell you the judge is cheap and the proxy hop is what hurts; someone who read only the KinD
report would tell you the exact opposite. The same condition on the same image differs by 6–64x
between the two.

So: **never quote an absolute per-task plugin figure without naming the cluster it came from.** That
is the practical rule, and it is the one thing from this study most likely to save you from a wrong
decision.

### It is not a timeout — we checked

A ~12 s per-task cost on one cluster and ~0.1 s on the other invites an obvious guess: a timeout or
a failing retry in the sidecar's egress path. A timeout leaves a signature — values piled on a round
number with a small spread. OpenShift's `auth-only` distribution runs 4.45–20.44 s with an SD of
3.57 s, broad and unimodal with nothing on a round number. That is contention and queueing. The
timeout hypothesis is dead, and the thing to investigate on a busy cluster is *why the intercepted
path is slow and jittery there*, which is a different question.

Note also that on both clusters the spread grows with the median. **Enabling the sidecar costs
reproducibility, not only latency** — which matters if you are using these benchmarks to detect
regressions.

## What does travel between clusters

The magnitudes did not reproduce. The structure did, and the structural findings are the ones with
consequences.

**Every serial tool call is authorized.** With `max_parallel_sessions=1`, no two calls can race for
one cache entry. OpenShift: 10 tasks attempted, 9 OK, 9 serial tool calls, 10 judge calls. KinD:
10/10/10/10. A judge cache with a time-to-live would have collapsed ten same-shape serial calls to
roughly one judge call on *either* cluster; it did not, twice, independently. **TTL decision caching
is ruled out.**

That leaves an open item worth naming, because it has a security consequence. At `p=4` the judge
count falls slightly short of the tool count. Two mechanisms explain that equally well — benign
single-flight deduplication, or fail-open under race — and they produce *identical counts*.
Distinguishing them is a code-reading task, not a measurement task. It is the one thing this study
could not settle.

**The judged-call ratio is ~1.** For every IBAC preset it lands at 0.94–1.00 on both clusters; for
`baseline` and `auth-only` it is exactly 0.00, which is correct and also confirms nothing else is
consulting the judge. This is worth knowing because the ratio looks alarming on small runs: a 5-task
leg can easily show "3 of 5 calls authorized," which invites the conclusion that the judge is being
skipped. At n=50 that reading dissolves. **Don't read a judged/tool ratio off a handful of calls.**

**Per-call cost is not a cross-benchmark constant.** There is an obvious shortcut for estimating what
a plugin will cost on tau2 or appworld without running them: take the per-tool-call delta measured on
cheap gsm8k legs and multiply by the target benchmark's tool-call count. **It does not work.** tau2
makes ~11 tool calls per task against gsm8k's ~1, so it tests the assumption directly:

| cluster | tau2 measured Δ/task | projected from gsm8k | measured/projected |
|---|---:|---:|---:|
| OpenShift | +70.8 s | 156.3 s | 0.45x |
| KinD | +46.8 s | 20.9 s | 2.24x |

(Figures from [plugin-study-xplat.md](results/v1.27-2026-09-12/plugin-study-xplat.md). The
per-platform reports quote 0.46x for OpenShift, because they pool the gsm8k baseline per leg with
warm-up excluded while the cross-platform generator pools it across conditions. The 2% gap is
immaterial to a number whose point is that it is off by a factor of two.)

The shortcut is not merely imprecise — it is **wrong in opposite directions on the two clusters**,
which rules out fixing it with a correction factor. Per-call cost is not the constant the arithmetic
needs it to be. Measuring a benchmark's plugin cost directly costs two legs; do that instead.

## Two traps in reading any of our latency numbers

These generalize past the plugin question, and they are the reason the designed study exists at all.

### Warm-up is one concurrency wave, not "the first few tasks"

Across three *no-sidecar* baseline legs, splitting the tasks at the first concurrency wave (the first
`num_parallel` tasks) gives a consistent warm-up penalty of 2.03x, 2.17x and 2.24x. Splitting at a
fixed cutoff of 10 tasks gives 1.09x, 1.40x and **0.72x** — the sign inverts. The wave is the real
boundary; a fixed task count is not, and our analyzers now derive the cutoff per leg from the
artifacts.

The consequence for small runs is severe. At `p=4`, a 5-task leg spends *four of its five tasks*
inside the warm-up transient. A per-task average from such a leg is mostly measuring startup.

### The deploy is the unit of replication, not the task

Every task in a leg shares one deployment. So a per-task confidence interval answers "how variable
are tasks within this one deploy?" — not "how variable is this configuration?" The honest noise
floor is the spread between two independent deploys of the *same* condition: **1.57 s** on OpenShift
and **0.071 s** on KinD, typically.

On both clusters that floor **exceeds every preset-to-preset step except the single dominant layer**.
So with two deploys per condition, a *ranking* of presets against each other is unavailable at any
task count. Adding tasks tightens the wrong interval. If you need the ranking, add deploys: roughly
`16 · σ² / δ²` deploys per condition for 80% power at effect size `δ`.

A side observation that falls out of the same table: OpenShift's two *baseline* deploys differ by
0.038 s, while its sidecar deploys differ by 1.5–2.6 s. The sidecar degrades deploy-to-deploy
reproducibility by roughly an order of magnitude.

## Recommendations

1. **Measure on the cluster whose numbers you intend to quote.** Use
   `reference/plugin_study_specs.json` unchanged so the design (nesting, crossover, n=50) is
   preserved. Two runs, one per platform, one at a time.
2. **Quote the structural findings freely** — serial calls are all authorized, TTL caching is ruled
   out, the judged ratio is ~1, per-call cost is not a cross-benchmark constant. These reproduced.
3. **Never quote an absolute per-task second figure, or a claim about which layer is expensive,
   without naming the cluster.** Both are cluster-local facts.
4. **Do not rank presets** from a two-deploy study. Say "the dominant layer is X and everything else
   is below the noise floor," which is what the data supports.
5. **Budget from the dominant layer only.** On a contended cluster, plan for the sidecar's presence;
   on a quiet one, plan for the judge's LLM call. The other layers are rounding error either way.
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

## Reproducing

One platform at a time — both clusters front the same LLM gateways, so parallel runs contend and the
latency numbers stop meaning anything.

```sh
BM_SPECS=reference/plugin_study_specs.json BM_LABEL=pstudy-<platform> \
  python3 reference/run-12.py        # ~1.5-2 h; detach it, see CLAUDE.md on long runs

python3 reference/gen-plugin-study.py <run.json> reference/plugin_study_specs.json \
  v1.27 '<platform label>' out.md <judge.ts>

python3 reference/gen-plugin-study-xplat.py reference/plugin_study_specs.json v1.27 xplat.md \
  '<label A>' <runA.json> <judgeA.ts> -- '<label B>' <runB.json> <judgeB.ts>
```

The cross-platform generator deliberately does **not** pool the two clusters into a single interval.
Pooling would assume the platforms are interchangeable, which is precisely the assumption this study
falsified.
