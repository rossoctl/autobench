# Upstream issue text — the per-task `GET /v1/models` probe

Paste-ready text for an issue on `github.com/Exgentic/exgentic`. It is a self-contained rewrite of
**Bug 3** in [`exgentic-agent-bug-report-20260901.md`](exgentic-agent-bug-report-20260901.md), which
stays the internal record — read that one for how Bug 3 relates to the #250/#251 work and for the
withdrawn figures. Deliberately different here: no cluster or gateway hostnames (the reproduction does
not need them), and the numbers are the full-matrix ones only.

---

## Title

`check_model_accessible_sync`: the per-task `GET /v1/models` probe fails whole tasks on a slow
endpoint — 10 s hard cap, no retry, not configurable

## Summary

As of `0.3.5.dev145`, `LiteLLMToolCallingAgentInstance.__init__` runs a `GET /v1/models` reachability
check before every task. The check is capped at **10 s regardless of the timeout the caller passes**,
has **no retry**, and **cannot be disabled or tuned**. When it trips, the agent raises
`HealthCheckError("Model endpoint for <model> is unreachable at <url>")` and the task fails **before
the model is ever called**.

On an endpoint that is reachable but slow to hand-shake, this converts a transient network stall into
a permanently failed task. In a benchmark harness that is a **pass-rate** loss, not just a latency
cost: the task is scored as not-passed and is indistinguishable, in the results, from the agent
answering incorrectly.

We hit this on 12 of 141 tasks (~9%) on one cluster while a second cluster running the identical image
lost 0 of 141. The endpoint the failing tasks could not reach answers `401` in 0.19 s when warm, and
48 of 50 tasks in the same leg reached it successfully from the same process.

## Why this is a regression

This is a side effect of the fix for #250 rather than an independent bug, and worth stating explicitly
because the two changes shipped together. Before that work, each task issued a `max_tokens=1`
completion probe. Retiring it was right — it cost tokens on every invocation, and a malformed request
classifies as `REACHABLE`, so it could report success without verifying anything. **But it was
replaced, not removed**, and the replacement is a hard gate rather than a best-effort check.

The old probe's failure mode was wasted latency and one inflated span count. The new probe's failure
mode is a lost task. That is a strictly worse failure for the same diagnostic.

## Where

| property | value | location |
|---|---|---|
| what it does | `GET /v1/models` against the OpenAI-compatible surface | `integrations/litellm/health.py` |
| when | **once per task**, in `LiteLLMToolCallingAgentInstance.__init__` | `agents/litellm_tool_calling/instance.py:88` |
| timeout | **10 s, hard-capped** — `min(timeout, _MODELS_PROBE_TIMEOUT)`, so a caller passing the documented `timeout=30.0` still gets 10 s | `health.py:529`, `_MODELS_PROBE_TIMEOUT = 10.0` |
| retries | **none** — the `backoff` machinery belongs to `acheck_model_accessible`, the *strict* completion check, not to this path | `health.py:466-486` |
| error text | `_fetch_models` swallows every exception into `(None, None)`, so a timeout is indistinguishable from a refusal; `check_models_endpoint` then reports "is unreachable" | `health.py:290-311, 360-363` |
| configurability | **none** — no environment variable, and the caller's timeout is clamped | — |

## Impact, measured

Two Kubernetes clusters, same agent image index (`sha256:d924a9ed…`, `0.3.5.dev145+g82008e9a9`; the
`linux/amd64` and `linux/arm64` children share revision `82008e9a9`), same benchmarks, same 12-leg
matrix, 141 tasks each. The only difference is the network path from the agent pod to the LLM gateway.

| agent version | cluster | tasks lost to the probe |
|---|---|---|
| `0.3.5.dev131` (pre-change) | slow-path cluster | **0** of 116 |
| `0.3.5.dev145` | slow-path cluster | **12** of 141 |
| `0.3.5.dev145` | fast-path cluster | **0** of 141 |

The losses are spread across every benchmark we run — gsm8k, tau2 and appworld — and across legs of
2 to 50 tasks, so it is not specific to one workload shape or one concurrency setting.

Two secondary effects, both relevant to anyone measuring with this agent:

- A killed task still writes a result row, with `status=ERROR`, zero LLM calls, zero tool calls and
  zero tokens, at ~10.5 s duration. Any per-task token statistic computed over all rows is therefore
  contaminated by a row for work that never happened — a zero-cost outlier that drags means and
  medians down and inflates the coefficient of variation.
- Because the probe precedes the model call, a raw pass rate cannot separate "the agent got it wrong"
  from "the agent never started". We had to add a second, adjusted pass rate over
  `total − probe_failures` to keep two clusters comparable at all.

## What we ruled out

- **Not routing or DNS.** From a shell in the failing agent pod, the same URL returns `401` in 0.19 s
  once warm. A fast `401` proves the path, the TLS chain and the DNS are all fine.
- **Not credentials.** A `401` is the expected answer to an unauthenticated probe; the authenticated
  calls in the same pod succeed.
- **Not a dead endpoint.** In the worst leg, 48 of 50 tasks reached the same endpoint from the same
  process. Whatever fails, fails intermittently.
- **Not the benchmark or the model.** It hits three benchmarks and four model classes, and the other
  cluster runs all of them clean on the identical image.

What is left is the handshake. Each task opens a **fresh** connection with no pooling, and a cold
DNS + TCP + TLS handshake to our gateway measured **3.1 s** from inside the pod. Add ordinary
variance, contention from concurrent tasks, and a VPN hop, and 10 s is not a comfortable margin. The
fast-path cluster has the same code and never comes close.

## Reproducing without our network

The trigger is only latency, so it reproduces with a deliberately slowed endpoint:

1. Point the agent at an OpenAI-compatible endpoint whose `GET /v1/models` responds in >10 s — a proxy
   with an injected delay, or `tc qdisc add dev eth0 root netem delay 6000ms` on the pod's interface,
   is enough. Note that a *fixed* delay above the cap fails every task; the interesting case is a
   delay distribution straddling 10 s, e.g. `netem delay 5000ms 6000ms`, which fails a fraction of
   them and looks exactly like a flaky agent.
2. Run any multi-task workload. Each task fails with `Model endpoint ... is unreachable` in ~10.5 s,
   having made no LLM call.
3. Confirm the endpoint is healthy from inside the same pod (`curl -s -o /dev/null -w '%{http_code}'`
   against the same URL) — it answers normally, which is the confusing part in the field.

## Suggested fixes, in preference order

1. **Retry the probe** using the backoff already present in the module. A handshake stall is precisely
   what `ErrorCategory.TRANSIENT` is for, and one retry would have absorbed every failure we saw.
2. **Hoist it out of the per-task path.** The model endpoint does not change between tasks of one run,
   so probing once per agent process keeps the diagnostic value at 1/N of the exposure. This is the
   change we would most like to see even if the timeout were generous.
3. **Let the cap be raised.** `min(timeout, _MODELS_PROBE_TIMEOUT)` silently makes the function's own
   `timeout` parameter a no-op above 10 s, which is surprising next to a documented default of 30 s.
   Either honour the argument or drop it from the signature.
4. **Make it skippable** via an environment variable, for operators who have already established
   reachability by other means.
5. **Distinguish a timeout from a refusal** in the error text. `_fetch_models` collapses both to
   `None`, and the resulting "is unreachable" sent us to check routing, firewalls and DNS for some
   time before we found the 10 s clamp — the message names a conclusion the code has not actually
   tested.

Even (5) alone would have saved us most of the investigation.

## Environment

| | |
|---|---|
| Agent image | `ghcr.io/exgentic/exgentic-a2a-tool_calling:latest` |
| Index digest | `sha256:d924a9ed615fba67ba0a1fa3f130e430e0768ad062491177bebaf8b945f1c1ef` |
| Children | `linux/amd64 sha256:7109b469…`, `linux/arm64 sha256:d11a8adf…`, both revision `82008e9a9` |
| Package version | `exgentic 0.3.5.dev145+g82008e9a9` |
| Agent | `tool_calling` |
| Benchmarks affected | gsm8k, tau2, appworld |
| Deployment | Kubernetes, one agent pod per run, no HTTP proxy on the model path |
