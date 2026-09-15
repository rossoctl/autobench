<!--
Send-ready text for the exgentic image authors. Everything below the rule is meant to be pasted
as-is: no cluster names, no internal hostnames, no instructions telling them what to run — the
evidence is ours. The internal record, including how this relates to the #250/#251 work and two
figures we withdrew, stays in exgentic-agent-bug-report-20260901.md. Keep the two in step.

RESOLVED — do NOT send this again. Fixed in 0.3.5.dev146+gff7ef6a37; all five suggestions were
taken. The report body is kept verbatim as the record of what we sent. Verification lives in the
"Resolution" section of exgentic-agent-bug-report-20260901.md.
-->

> **✅ RESOLVED in `exgentic 0.3.5.dev146+gff7ef6a37`** (index `sha256:c2b6fdb5…`), verified
> 2026-09-15. All five suggested changes were taken: a retry (2 attempts, 0.5 s apart, transport
> failures only), the caller's timeout honoured with an `EXGENTIC_MODEL_PROBE_TIMEOUT` override,
> a per-process success memo so the probe no longer runs per task, an
> `EXGENTIC_SKIP_MODEL_PROBE` opt-out, and per-cause error text in place of "is unreachable".
> The 3 legs that lost 4 of 61 tasks now run 61/61 clean. **Do not re-send this text** — it is kept
> as the record of what was reported. Verification detail:
> [`exgentic-agent-bug-report-20260901.md`](exgentic-agent-bug-report-20260901.md#bug-3-resolution).

# The per-task `GET /v1/models` probe fails whole tasks on a slow first contact

**Reported against:** `ghcr.io/exgentic/exgentic-a2a-tool_calling:latest`, index
`sha256:d924a9ed615fba67ba0a1fa3f130e430e0768ad062491177bebaf8b945f1c1ef`,
`exgentic 0.3.5.dev145+g82008e9a9`. Code references below were read out of that image, not from
a source checkout, so they are what ships.

---

## Summary

`LiteLLMToolCallingAgentInstance.__init__` runs a `GET /v1/models` reachability check before every
task. The check is **capped at 10 s regardless of the timeout the caller passes**, has **no retry**,
and **cannot be disabled or tuned**. When it trips, the agent raises

```
HealthCheckError: Model endpoint for <model> is unreachable at <url>
```

and the task fails **before the model is ever called**.

The endpoint in question is not down. On our slower cluster it answers in **0.20 s at the median**,
measured from inside the agent's own container with the agent's own code path. But the distribution has
a long right tail on *first contact*, and one draw from that tail crossed the cap: **10.09 s, failed —
while the very next attempt on the same path returned in 0.18 s.** Because there is no retry, that
single excursion is not a slow task, it is a lost task.

Across a 141-task benchmark matrix this cost us **12 tasks on one cluster and 0 on another**, same
image, same benchmarks — the only difference being the network path to the model endpoint.

We think the fix is small (a retry, or hoisting the probe out of the per-task path) and that the
diagnostic value survives it intact. Detail and measurements below.

## The code, as shipped

| | | |
|---|---|---|
| probe runs | **once per task**, in `LiteLLMToolCallingAgentInstance.__init__` | `agents/litellm_tool_calling/instance.py:88` |
| cap | `_MODELS_PROBE_TIMEOUT = 10.0` | `integrations/litellm/health.py:246` |
| clamp | `check_models_endpoint(model, logger, timeout=min(timeout, _MODELS_PROBE_TIMEOUT))` | `health.py:529` |
| retries | **none on this path** — the `backoff` machinery belongs to `acheck_model_accessible`, i.e. the `strict=True` completion check | `health.py:466-486` |
| transport | `urllib.request.urlopen(request, timeout=timeout)`, a new connection per call, no pooling | `health.py:290-311` |
| error | `_fetch_models` returns `(None, None)` for anything that is not an HTTP response; `check_models_endpoint` maps that to "is unreachable" | `health.py:311`, `health.py:360-363` |
| configurability | none — no environment variable, and the caller's timeout is clamped | — |

Two details worth calling out, because both surprised us:

**The `timeout` parameter of `check_model_accessible_sync` cannot raise the cap.** Its signature says
`timeout: float = 30.0` and its docstring says *"Timeout in seconds for the endpoint probe (default:
30s)"*, but `min(timeout, _MODELS_PROBE_TIMEOUT)` means the probe never gets more than 10 s. Any value
above 10 is silently discarded, so the documented default is unreachable through the documented knob.

**`urlopen`'s `timeout` does not bound name resolution.** `socket.getaddrinfo` runs inside
`create_connection` and takes no timeout, so on a host with slow DNS the total wall time exceeds the
nominal 10 s. Not our dominant cost — in-cluster DNS resolved in ~0.00-0.07 s — but on a directly
resolving host we measured a cold lookup for the same name at **3.09 s**, which would consume a third
of the budget before a packet reaches the endpoint.

## Impact, measured

Two Kubernetes clusters, the same agent image index (`d924a9ed…`; its `linux/amd64` and `linux/arm64`
children share revision `82008e9a9`), the same 12-leg benchmark matrix, 141 tasks each. The only
material difference is the network path from the agent pod to the model endpoint.

| agent | cluster | tasks lost to the probe |
|---|---|---|
| `0.3.5.dev131` (before the probe change) | slower path | **0** of 116 |
| `0.3.5.dev145` | slower path | **12** of 141 |
| `0.3.5.dev145` | faster path | **0** of 141 |

The 12 losses were spread across three benchmarks and legs of 2 to 50 tasks — not one workload shape,
not one concurrency setting. In the worst leg, **48 of 50 tasks reached the same endpoint from the same
process**; the other 2 died on the probe.

Two consequences that may matter to you beyond the failure itself:

- A killed task still produces a result row, at `status=ERROR` with zero LLM calls, zero tool calls,
  zero tokens and ~10.5 s duration. Any per-task token or latency statistic computed over all rows is
  then contaminated by a row for work that never happened.
- Because the probe precedes the model call, a raw pass rate cannot separate *the agent answered
  incorrectly* from *the agent never started*. We had to add a second pass rate over
  `total − probe_failures` to keep two clusters comparable at all.

## What we measured directly

All of the following was run from a container on the agent image, in the namespace where the failures
occurred, through the probe's own code path (`urllib`, one fresh connection per call, `timeout=10.0`)
and sending no credentials — a `401` is `REACHABLE` to `check_models_endpoint`, so an unauthenticated
probe exercises exactly what the agent exercises.

**1. The distribution is fine at the median and bad in the tail.** 30 sequential probes from one pod:

| min | p50 | p90 | p95 | max | over the 10 s cap |
|---|---|---|---|---|---|
| 0.15 s | 0.20 s | 0.26 s | 0.90 s | **10.09 s** | **1 of 30** |

The 10.09 s sample was the pod's **first** contact with the endpoint. It failed with the socket
timeout. The 29 that followed on the warmed path ranged 0.15-0.90 s. The mean of 0.56 s is entirely an
artefact of that one sample, which is the point: the median tells you nothing about your exposure here,
because the probe is a per-task all-or-nothing gate against a heavy-tailed quantity.

**2. Every slow attempt we recorded was absorbed by the next attempt on the same path.** Seven
attempts of ≥0.85 s occurred across all our runs; the following attempt took ≤0.23 s in every case:

| slow attempt | next attempt on the same path |
|---|---|
| **10.09 s (failed)** | 0.18 s |
| 3.37 s | 0.19 s |
| 1.27 s | 0.15 s |
| 1.22 s | 0.15 s |
| 1.03 s | 0.18 s |
| 0.90 s | 0.22 s |
| 0.88 s | 0.23 s |

7 of 7. This is the single strongest argument for a retry: the condition is transient by construction,
and one immediate re-attempt would have converted every failure we have seen into a task that ran.

**3. First contact from a fresh pod is systematically slower than its own retry — 6 of 6.** New pod,
two probes back to back:

| pod | first contact | immediate retry |
|---|---|---|
| 1 | 0.45 s | 0.25 s |
| 2 | 0.42 s | 0.25 s |
| 3 | 0.31 s | 0.24 s |
| 4 | 1.22 s | 0.15 s |
| 5 | 1.27 s | 0.15 s |
| 6 | 1.03 s | 0.18 s |

Median 0.74 s against 0.21 s — a ~3.5× cold-path penalty, in the same direction every time. Nothing
here crossed the cap on the day we measured; the network was in better shape than during the matrix.
That is the nature of the defect: the penalty is always present, its magnitude is a property of the
path at that moment, and the agent turns any excursion past 10 s into a lost task with no second look.

**4. Neither concurrency nor process freshness is the trigger.** We checked both, because both were
plausible and both would have implicated us rather than the probe:

- 4 concurrent probes, 16 samples: 0.15-0.47 s, **0 failures**. Parallel tasks colliding is not it.
- 10 fresh Python processes in an already-warm pod, 2 probes each: **20 of 20 succeeded**, slowest
  first attempt 3.37 s (retry 0.19 s). A new process on a warm path is fine.

What predicts the slow attempt is the *path* being cold — a new pod, or an idle gap — not the process
or the load. After a 120 s idle gap, first attempts rose again (median 0.36 s against 0.21 s warm).

**5. A service-mesh dataplane roughly doubles the cold penalty but does not explain the excursion.**
Same image, same namespace, mesh dataplane disabled on the pod: first contact median 0.21 s (n=3)
against 0.74 s in-mesh (n=6). So our environment contributes, and we are not claiming otherwise — but
0.21 s and 0.74 s are both two orders of magnitude below the cap. What crosses 10 s is the tail, and
the tail exists on both.

## What we ruled out

- **Routing, DNS, TLS.** From a shell in the affected pod the same URL returns `401` in 0.19 s once
  warm. A fast `401` proves the route, the chain and the resolution are all sound.
- **Credentials.** `401` is the expected answer to an unauthenticated probe, and the authenticated
  calls from the same pod succeed.
- **A dead or overloaded endpoint.** 48 of 50 tasks in the worst leg reached it from the same process,
  and 29 of 30 probes in our instrumented run answered in under a second.
- **The benchmark, the model, the workload shape.** It hits three benchmarks and four model families,
  and a second cluster runs all of them clean on the identical image.

What remains is first-contact latency on a path that is healthy but not fast — which is a condition the
agent cannot control and, we would argue, should not fail a task over.

## The argument, stated plainly

Retiring the old `max_tokens=1` completion probe was right, and the docstring's reasoning is sound: a
generation probe costs tokens on every invocation, and a malformed request classifies as `REACHABLE`,
so it could report success while verifying nothing. We are not asking for it back.

But it was **replaced, not removed**, and the replacement is a hard gate where the old one was merely
wasteful. The old probe's worst case was a wasted round trip and one inflated call count. The new
probe's worst case is a task that never runs, reported as a failure indistinguishable from the model
getting the answer wrong. For a diagnostic that is meant to produce a *better error message*, that is
an expensive trade — and it is paid per task, so exposure scales with the length of the run.

## Suggested changes, in the order we would value them

1. **Retry once, with the backoff already in the module.** A first-contact stall is exactly
   `ErrorCategory.TRANSIENT`, and 7 of 7 slow attempts we recorded were followed by a sub-quarter-second
   success. This alone would have cost us zero tasks.
2. **Hoist the probe out of the per-task path.** The model endpoint does not change between tasks of
   one run, so probing once per agent process retains the diagnostic at 1/N of the exposure. We would
   want this even with a generous timeout, because the per-task placement is what makes a rare event
   into a recurring one.
3. **Honour the caller's `timeout`, or drop it from the signature.** As it stands,
   `check_model_accessible_sync(timeout=30.0)` is documented, accepted, and silently reduced to 10.
4. **Allow it to be skipped** via an environment variable, for operators who have established
   reachability by other means and would rather fail on the real call.
5. **Distinguish a timeout from a refusal in the message.** `_fetch_models` collapses timeout, refusal,
   DNS failure and TLS failure into `(None, None)`, and the resulting *"is unreachable"* sent us
   through routing, firewall and DNS checks before we found the 10 s clamp. The message asserts a
   conclusion the code has not tested. Even this change alone would have saved us most of the
   investigation.

## Provenance

| | |
|---|---|
| Image | `ghcr.io/exgentic/exgentic-a2a-tool_calling:latest` |
| Index digest | `sha256:d924a9ed615fba67ba0a1fa3f130e430e0768ad062491177bebaf8b945f1c1ef` |
| Children | `linux/amd64 sha256:7109b469…`, `linux/arm64 sha256:d11a8adf…`, both `org.opencontainers.image.revision = 82008e9a9…` |
| Package | `exgentic 0.3.5.dev145+g82008e9a9` |
| Agent | `tool_calling` |
| Benchmarks affected | gsm8k, tau2, appworld |
| Deployment | Kubernetes, one agent pod per run, no HTTP proxy on the model path |
| Code references | read from `/app/exgentic/src/exgentic/` inside the image above |
