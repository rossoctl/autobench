# exgentic agent — two defects found while benchmarking (2026-09-01)

Reported by the AutoBench Service team. Both reproduce on a stock agent image with no local
patches. Bug 1 is a correctness problem (token telemetry is silently lost); Bug 2 is pure wasted
latency. They are independent.

> ## ✅ Both fixed upstream — resolution note added 2026-09-14
>
> **The rest of this document describes the defects as they were, against the digest in the
> Environment table below. That digest is superseded — do not use this page to reason about a current
> agent.** Superseded by index `sha256:d924a9ed615fba67ba0a1fa3f130e430e0768ad062491177bebaf8b945f1c1ef`,
> package version `exgentic 0.3.5.dev145+g82008e9a9`. The image tag did not change — it is still
> `:latest` — so **only a digest comparison tells you which code you are running**; the local podman
> cache served the old `:latest` for six weeks and made the fixes look absent.
>
> **Both architectures carry the fix, and they are the same source revision.** Read off the registry
> with `skopeo inspect --raw docker://ghcr.io/exgentic/exgentic-a2a-tool_calling:latest` — the index is
> a two-platform OCI image index (plus one attestation manifest per platform):
>
> | platform | child digest | used by |
> |---|---|---|
> | `linux/amd64` | `sha256:7109b46933537873830fa640127cd7f4d2efc5138e911918d7981af8b7c07ec1` | OpenShift (`ykt2`) |
> | `linux/arm64` | `sha256:d11a8adf50220f1b242b80bef8d38cacfd318539ba412f2f6684ae3b03b7fbf1` | KinD (Apple Silicon) |
>
> Both children carry `org.opencontainers.image.revision =`
> `82008e9a9ad28a304f308c621c619a18ae35303d`, which is the `g82008e9a9` in the package version above —
> so the two clusters ran the *same commit*, not merely the same tag, and the arm64 leg's results are
> not a different code base. Checked again on 2026-09-15T03:02Z: the index digest still resolves
> to `d924a9ed…`, i.e. `:latest` has not moved since the matrices were run. **Record both children, not
> just your own arch** — a single-arch digest looks like full provenance and is not.
>
> **The issue numbering does not line up with ours, and three distinct things are involved:**
>
> | | ours | upstream | status |
> |---|---|---|---|
> | span loss on warm agents | **Bug 1** (below) | **#250** | fixed — `_get_parent_context` now uses `getattr(ctx, "otel_context", None)` and falls back to the ambient OTEL context instead of raising `AttributeError` into a swallowed handler. A second defect fixed with it: the tracer is process-global, so `_init_otel` never re-ran on a warm process and the per-session log stayed pinned to the first run's directory. |
> | the `max_tokens=1` probe | **Bug 2** (below) | *no issue — changed as part of the same work* | **measured gone**, across all four model classes: 12 legs, 869 `chat` spans, `request_max_tokens` `null` on every one. But it was **replaced, not removed** — see the regression below, which is a direct consequence. |
> | LiteLLM response caching on by default | *we never reported this* | **#251** | fixed upstream, and **honored on our deployment** — the `a2a` command defaults it off via a `model_fields_set` check, and because that check lets an explicit value win in either direction `registry.py` now pins `EXGENTIC_LITELLM_CACHING=false` as well. Verified in the running pod: the startup line `✓ LiteLLM response caching disabled via EXGENTIC_LITELLM_CACHING`, `get_settings().litellm_caching == False`, and no `/app/.cache/exgentic/litellm` directory. |
>
> So upstream #251 is **not** our Bug 2 — and, as it turned out, **not the cause of the completion
> replays either.** The replays are real; they happen at the **LLM gateway**, outside the agent.
>
> **What this changes for us, and what it does not.** The `llm` column now counts real LLM calls
> one-for-one, with no probe offset to subtract — see `docs/BENCHMARKS_PRIMER.md`, and note that this
> makes `llm` counts **non-comparable across the version boundary**. The damage detector is unchanged
> and stays structural (`llm ≤ 1` with `tool ≥ 2`); a bare "one `chat` span" test is now actively
> wrong, because one `chat` span is the *healthy* shape for a one-shot gsm8k task.
>
> **The fresh-deploy-per-run workaround is retained — but the reason for it has changed.** Upstream
> states it is no longer needed for telemetry correctness, and *that* part is now measured and
> confirmed: `reference/warm_reuse_specs.json`, run on OpenShift 2026-09-14 (one cold reference leg,
> two serial reuse legs, one reuse leg at `p=4`; 20 tasks, all `succeeded`, pass 1.0 throughout), gave
> **complete token attribution on every warm row** — 5 `chat` spans per leg, all `counted: true`, no
> row matching the structural detector. The span *presence* is the evidence here; the fact that warm
> token totals came out byte-identical to the cold leg is **not** independent corroboration, because a
> gateway replay (below) returns the very same stored `usage`. Bug 1 is fixed on a warm process, not
> merely on a fresh one.
>
> **The completion replay is real, but it is not the agent's and not #251's — it is the LLM gateway.**
> Measured directly, with the agent code out of the picture: four POSTs to
> `{OPENAI_API_BASE}/v1/chat/completions` issued from a shell inside the agent pod, three of them
> byte-identical and the fourth with one sentence appended.
>
> | call | latency | response `id` (tail) | `completion_tokens` |
> |---|---|---|---|
> | 1 — fresh nonce | 2.969 s | `GMHy8TLYwl6U` | 274 |
> | 2 — identical body | **0.131 s** | `GMHy8TLYwl6U` | 274 |
> | 3 — identical body | **3.013 s** | `GMHy8TLYwl6U` | 274 |
> | 4 — one sentence appended | 7.391 s | `1NdXijnEEQw3` | 465 |
>
> A response `id` is minted per request, so **the same `id` coming back three times is a replay of one
> stored response** — served by `ete-litellm.ai-models.vpc.res.ibm.com`, not by anything in the agent.
> The same test inside a KinD agent pod against the *other* gateway
> (`ete-litellm.ai-models.vpc-int.res.ibm.com`, a separate deployment with its own key table) behaves
> identically: 6.283 s then 0.561 s and 0.595 s on one `id`, a new body 5.067 s on a new one. **Both
> our gateways do this.** The TTL is bracketed at **7–15 minutes**: one stored response was still
> being replayed at t+60 s, t+180 s and t+420 s and was gone by t+900 s.
>
> Two claims an earlier draft of this note made, both wrong, both mine:
>
> - **"The cache is per process, not gateway-side" — withdrawn.** It was inferred from a cold leg
>   paying full price on prompts an earlier leg had already sent through the same gateway, but that
>   comparison is confounded by *time*: every cold leg followed a ~13-minute gap while every warm leg
>   followed by seconds. A `kubectl rollout restart` settles it. On a **brand-new pod** — empty
>   process, empty everything — the same five gsm8k prompts came back at **0.08–1.43 s** against
>   **2.7–5.9 s** on a genuinely cold gateway, with byte-identical output-token totals. Nothing that
>   survived the restart lived inside the agent.
> - **Latency is not a reliable cache-hit detector.** Call 3 above is a replay that took 3.013 s,
>   indistinguishable by the clock from call 1's miss. Compare **response `id`s**; use latency only as
>   a hint.
>
> **What the replay damages.** A replayed response carries the stored `usage`, so tokens get
> attributed to a call the model never made and that call's latency measures a cache lookup. It is
> keyed on the request body, so it bites wherever a run repeats a prompt inside the TTL — and the
> canonical matrix does repeat prompts: legs #1, #2, #3 and #5–#8 all open with the same five gsm8k
> tasks. Pass rates and input-token counts are untouched. What our matrices should **not** be read as
> claiming is that gsm8k per-call latency and output tokens are independent measurements across those
> legs.
>
> **The fresh-deploy-per-run rule stays, but it no longer earns its keep the way we thought.** It does
> not buy telemetry correctness any more (Bug 1 is fixed) and it never could have avoided the gateway
> cache (a fresh pod dials the same gateway). What it does still buy is unrelated to both: a *newly
> created* pod re-pulls `:latest` under `imagePullPolicy: Always`, whereas a long-lived one serves a
> stale digest indefinitely, and a Deployment left standing keeps serving its old `api_base`.
>
> The **`EXGENTIC_LITELLM_CACHING=false`** pin in `registry.py` is kept as defence in depth, not as a
> fix: `utils/settings.py` still declares `litellm_caching: bool = True`, and only the `a2a` command
> overrides it when unset, so pinning it removes our dependence on that one code path. Rolling it out
> (Service image v1.28, both clusters) demonstrably changed nothing about the replays — as expected,
> now that they are known to be gateway-side. Note that the pin cannot be confirmed from
> `GET /benchmarks`, which omits `extra_env`; read `GET /benchmarks/{name}` or the pod's env.

---

## Bug 3 — the replacement health probe fails tasks outright on a high-latency gateway (found 2026-09-14, `0.3.5.dev145`)

> **Still open upstream.** Paste-ready issue text, with our cluster and gateway names stripped and only
> full-matrix numbers: [`exgentic-issue-models-probe.md`](exgentic-issue-models-probe.md). Keep the two
> in step if either changes.

**This is a regression introduced by the same change that fixed Bug 1 and retired Bug 2's probe**, and
it is why the `max_tokens=1` probe disappeared: the probe was **replaced**, not removed.
`check_model_accessible_sync` now runs the unbilled reachability check instead of a completion call —
deliberately, and the docstring's reasoning is sound (a generation probe costs tokens on every
invocation, and a malformed request classifies as `REACHABLE`, so it could report success without
verifying anything; `strict=True` adds it back).

The replacement, however, is a hard gate in front of every task:

| property | value | where |
|---|---|---|
| what it does | `GET /v1/models` against the OpenAI-compatible surface | `integrations/litellm/health.py` |
| when it runs | **once per task** — in `LiteLLMToolCallingAgentInstance.__init__` | `agents/litellm_tool_calling/instance.py:88` |
| timeout | **10 s, hard-capped** — `min(timeout, _MODELS_PROBE_TIMEOUT)`, so a caller passing the documented `timeout=30.0` still gets 10 s | `health.py:529`, `_MODELS_PROBE_TIMEOUT = 10.0` |
| retries | **none** on this path (the `backoff` machinery belongs to `acheck_model_accessible`, the *strict* completion check) | `health.py:466-486` |
| failure mode | `_fetch_models` swallows every exception into `(None, None)`, so a timeout is indistinguishable from a refusal, and `check_models_endpoint` raises `HealthCheckError(f"Model endpoint for {model} is unreachable at {url}")` | `health.py:290-311, 360-363` |
| configurability | **none** — no env var, and the caller-supplied timeout is clamped | — |

**Net effect: a single slow `GET /v1/models` kills the task before the model is ever called.** The
task is reported as failed, so unlike Bugs 1 and 2 this costs *pass rate*, not just telemetry.

### Measured

Same cluster, same gateway, same benchmark, only the agent digest differs:

| agent | platform | unreachable failures |
|---|---|---|
| `15a682ce` (pre-fix) | KinD | **0** across all 12 legs (116 gsm8k+tau2 tasks) |
| `d924a9ed` (dev145) | KinD | **12 of 141 tasks** across all 12 legs |
| `d924a9ed` (dev145) | OpenShift | **0** across all 12 legs |

Per leg on KinD: #1 1/1, #2 1/10, #3 2/50, #4 2/5, #5 1/5, #7 1/5, #10 3/20, #12 1/20 — so it hits
every benchmark (gsm8k, tau2, appworld) and is not concentrated in one leg shape.

It is a *latency* interaction, not a misconfiguration — the endpoint answers `401` in 0.19 s warm from
both the host and inside the agent pod, and 48 of 50 tasks in leg #3 succeeded against the same
endpoint in the same process. The KinD gateway is VPN-routed
(`ete-litellm.ai-models.vpc-int.res.ibm.com`, 9.47.x), each task opens a *fresh* connection with no
pooling, and a cold DNS+TCP+TLS handshake measured 3.1 s from inside the pod. OpenShift never trips it
because its path to the gateway is fast.

⚠️ **Two figures previously quoted here came from an earlier, partial KinD run and are withdrawn.**
That run reported 11 of 66 tasks in its first four legs, and leg #5 (`auth-only`, sidecar) losing 3 of
5 against leg #3's 7 of 50 — on which basis this section claimed the AuthBridge sidecar "makes it
markedly worse". **The full matrix does not reproduce that.** Sidecar legs #5–#8 lost 2 of 20 tasks
(10%); no-sidecar legs #1–#4 lost 6 of 66 (9%). The sidecar claim is therefore unsupported and should
not be carried upstream; the loss rate on this cluster is ~9% either way. The earlier run's higher
rate is consistent with a *worse network moment*, which is exactly what a latency-triggered bug looks
like — the rate is a property of the path on the day, not a stable constant. Do not quote it as one.

### Instrumented directly, 2026-09-15

Measured from a pod on the agent image in `team1`, through the probe's own code path (`urllib`, fresh
connection per call, `timeout=10.0`, no credentials — a `401` is `REACHABLE`). Scripts were piped in
over `kubectl exec` / a ConfigMap; `/tmp` is not shared into the podman VM, so a bind mount fails.

- **30 sequential probes, one pod:** min 0.15 s, p50 **0.20 s**, p90 0.26 s, p95 0.90 s, max **10.09 s**
  → **1 of 30 failed**, and it was the pod's *first* contact. The 29 that followed ran 0.15-0.90 s.
- **Every slow attempt was absorbed by the next one on the same path — 7 of 7.** 10.09 s (failed) →
  0.18 s; 3.37 → 0.19; 1.27 → 0.15; 1.22 → 0.15; 1.03 → 0.18; 0.90 → 0.22; 0.88 → 0.23. This is the
  retry argument, and it is the strongest thing we have.
- **First contact from a fresh pod is slower than its own retry in 6 of 6 pods** — median 0.74 s vs
  0.21 s, a ~3.5× cold-path penalty in the same direction every time.
- **Ruled out two explanations that would have implicated us, not the probe:** 4 concurrent probes
  (16 samples, 0.15-0.47 s, 0 failures) and 10 fresh *processes* in a warm pod (20/20 succeeded). The
  predictor is a cold *path* — new pod, or an idle gap (after 120 s idle, first-attempt median rose to
  0.36 s) — not load and not process freshness. ⚠️ Note I nearly repeated the time-confound mistake
  from the caching investigation here: "fresh process" was again the intuitive cause and again wrong.
- **The mesh dataplane roughly doubles the cold penalty but does not explain the excursion:** with
  `istio.io/dataplane-mode: none`, first-contact median 0.21 s (n=3) vs 0.74 s in-mesh (n=6). Both are
  two orders of magnitude below the cap.
- **Host-side, DNS is the expensive part on a cold cache:** `time_namelookup` **3.09 s** of a 3.67 s
  total for the same name, and `urlopen`'s timeout does not bound `getaddrinfo`. In-cluster DNS resolved
  in ~0.00-0.07 s, so this is not what bit us — but it is a second way to exceed a 10 s budget.

Every code reference in this section and in the upstream text was **read out of the image**
(`/app/exgentic/src/exgentic/`), not from a checkout: `health.py:246` (`_MODELS_PROBE_TIMEOUT = 10.0`),
`health.py:529` (the clamp), `instance.py:88` (per-task call site), and
`check_model_accessible_sync(timeout: float = 30.0)` whose docstring promises 30 s the clamp can never
grant. The probe pod reported image ID `sha256:d924a9ed…`, which also confirms the KinD node cache
holds the dev145 index — the operational check the artifacts do not record.

### Suggested fix, in preference order

1. **Retry the probe** with the backoff already present in the module — a transient handshake stall is
   exactly what `ErrorCategory.TRANSIENT` exists for.
2. **Let the cap be raised.** The `min(timeout, _MODELS_PROBE_TIMEOUT)` clamp makes the function's own
   `timeout` parameter a no-op above 10 s, which is surprising given the documented default of 30 s.
3. **Make it skippable** (env var), and/or **hoist it out of the per-task path** — the model endpoint
   does not change between tasks of one run, so probing once per agent process would keep the
   diagnostic value at 1/N the exposure.
4. **Distinguish timeout from refusal** in the error text; `_fetch_models` currently collapses both to
   `None`, and "is unreachable" sent us to check routing when routing was fine.

## Environment

| | |
|---|---|
| Agent image | `ghcr.io/exgentic/exgentic-a2a-tool_calling:latest` |
| Image digest (index) | `sha256:15a682ceb1deffa72a1b9a78b7ad03ff5614bb86c405cbc33cd9ff35395ec2ee` |
| Agent | `tool_calling`, benchmark `gsm8k` (also seen on `tau2`) |
| Model | `openai/Azure/gpt-5-mini-2025-08-07` via an OpenAI-compatible LiteLLM gateway |
| Agent env | `EXGENTIC_OTEL_ENABLED=true`, `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`, exporting to an otel-collector that forwards to MLflow |
| Runner | reproduced with **both** `EXGENTIC_DEFAULT_RUNNER=direct` and `=service` |

Confirmed identically on two unrelated clusters (single-node KinD on arm64, and OpenShift on amd64),
so it is not environment-specific.

---

## Bug 1 — `TraceLogger._write_otel` throws on the 2nd and later runs against a warm agent, losing the LLM span

### Symptom

The **first** run after an agent pod starts records LLM telemetry correctly. Every **subsequent**
run against the same agent process loses the span for the successful LLM call, so downstream token
accounting under-reports that task's usage even though the task ran and passed.

The task itself is unaffected — tool calls happen, evaluation passes, latencies are recorded. Only
the telemetry is lost, which makes this easy to miss.

**It is even easier to miss than "tokens are 0", because what the damaged row reports depends on the
model.** What survives is Bug 2's probe span, and the probe's own usage differs by model class:

| model | probe outcome | damaged row reports |
|---|---|---|
| `gpt-5-mini` (reasoning) | rejected, `BadRequestError`, no usage | `in=0, out=0` |
| `claude-sonnet-5` | **succeeds** | `in=8, out=1` |
| `gemini-2.5-pro` | **succeeds** | `in=1, out=0` |

So the two bugs interact: **Bug 2 masks Bug 1 on non-reasoning models.** A consumer checking
`tokens == 0` catches only the reasoning-model case. We shipped exactly that check and it reported
our data as clean while 15 rows were in fact damaged — a reviewer spotted "8 tokens in, 1 out" on a
task that had made 11 tool calls. The model-independent signal is structural: **`llm_count <= 1`
together with `tool_count >= 2` is impossible**, because every tool call needs a preceding model
turn.

### Reproduction (100% reliable)

1. Start a fresh agent pod.
2. Drive one benchmark run (any size). → telemetry correct.
3. Drive a **second** run against the same pod, no restart. → telemetry lost.

Measured, `gsm8k`, `max_parallel_sessions=1` (so this is **not** a concurrency issue):

| | task 0 | task 1 |
|---|---|---|
| run A — first after pod start | `llm=2`, in=320 out=87 ✅ | `llm=2`, in=283 out=23 ✅ |
| run B — same pod, ~30s later | `llm=1`, **in=0 out=0** ❌ | `llm=1`, **in=0 out=0** ❌ |

Repeated a third time on a pod that had served one 50-task run: the next 1-task run again gave
`llm=1, in=0, out=0`.

### Evidence — the exception

Logged by the agent container once per lost span, and **zero times during run A**:

```
TraceLogger._write_otel failed
Traceback (most recent call last):
  File "/app/exgentic/src/exgentic/integrations/litellm/trace_logger.py", line 223, in _write_otel
    self._emit_llm_span(kwargs, response_obj, status, start_time, end_time)
  File "/app/exgentic/src/exgentic/integrations/litellm/trace_logger.py", line 239, in _emit_llm_span
    parent_ctx = self._get_parent_context(kwargs)
                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/exgentic/src/exgentic/integrations/litellm/trace_logger.py", line 155, in _get_parent_context
    trace_id_hex = ctx.otel_context.trace_id
                   ^^^^^^^^^^^^^^^^
AttributeError: 'NoneType' object has no attribute 'otel_context'
```

So `ctx` is `None` in `_get_parent_context`. The count correlates exactly: a 2-task run B produced
**2** `_write_otel failed` entries and lost **2** spans; run A produced none.

### Evidence — the spans themselves

Read back from the tracing backend for the same benchmark task. Each task normally produces **two**
`chat` spans, which are two *different* LLM calls (see Bug 2), and only the second is the real one:

**Healthy task (run A)** — 2 chat spans:

| span | attrs | notes |
|---|---|---|
| probe | `gen_ai.request.max_tokens=1`, `error.type=BadRequestError` | no usage (expected — see Bug 2) |
| real call | `gen_ai.usage.input_tokens=320`, `gen_ai.usage.output_tokens=87`, `finish_reasons=[tool_calls]`, `gen_ai.response.id=chatcmpl-…` | usage present |

**Affected task (run B)** — 1 chat span:

| span | attrs | notes |
|---|---|---|
| probe | `gen_ai.request.max_tokens=1`, `error.type=BadRequestError`, `has_usage=False` | the **real call's span is absent entirely** |

So this is not "a span written without usage attributes" — the successful call's span is **never
emitted**, consistent with `_emit_llm_span` raising before it writes.

### Impact

- LLM token usage silently under-reports for every run after the first on a given pod. Anything
  built on that telemetry (cost accounting, per-task token analysis) is wrong without any error
  surfacing to the caller.
- It is easy to misdiagnose. We initially attributed the resulting data loss to concurrency and to a
  benchmarking-platform difference; both were wrong. The real variable was whether the run was the
  first one against that pod.

### Suggested fix

`_get_parent_context` assumes `ctx` is non-`None`. Two things worth considering:

1. **Guard it** — if `ctx` (or `ctx.otel_context`) is `None`, fall back to the current OTEL context
   or emit the span without a parent, rather than raising and dropping the span.
2. **Root cause** — the per-process/per-session context appears to be torn down when a run
   completes and is not re-established for the next run, so the second run finds it `None`. A
   guard alone would keep the tokens but the span may end up unparented; re-establishing the
   context per request is probably the more correct fix.

### Our workaround

We now deploy a fresh agent before **every** run. Where applied it removes the loss completely: a
10-task run went from 1 affected task to 0/10, and the gsm8k legs of two full 12-run matrices went
from 47 affected rows to 0. It costs ~40–60s per run, so it is viable for us but is clearly a
workaround.

The residual confirms the mechanism rather than contradicting it. Three legs of those matrices still
reused a warm agent, and they are **exactly** the legs with damaged rows: 10 of 20 tau2 tasks and 3
of 15 appworld tasks on OpenShift, 2 of 18 appworld tasks on KinD — 15 of 272 rows overall. Every
leg that deployed fresh is clean, on both clusters.

Two further observations that may help localise the fault:

- **The loss is partial and ordered.** On the 20-task tau2 run, tasks 0–9 lost their spans and tasks
  10–19 recorded correctly, in a run whose first 10 task ids duplicated those of the immediately
  preceding 10-task run on the same pod. It is not "all tasks after the first run fail" — something
  recovers partway through.
- **The corruption is visible without any telemetry backend**, via the arithmetic: that 20-task run
  reported *fewer* total input tokens (692,864) than the 10-task run before it (936,094). Twice the
  tasks, two thirds the tokens.

---

## Bug 2 — the per-task `max_tokens=1` probe is rejected by reasoning models

### Symptom

Each task issues an extra LLM call with `gen_ai.request.max_tokens=1` before the real work. With a
**reasoning** model that call fails with `BadRequestError` and yields nothing; with a non-reasoning
model it succeeds. Either way it is counted as an LLM call.

### Evidence — same benchmark task, two models

`gpt-5-mini` (reasoning) — the probe **fails**:

| span | `max_tokens` | error | usage | dur |
|---|---|---|---|---|
| probe | **1** | **`BadRequestError`** | none | 9.58s |
| real call | — | none | 320 in / 87 out | 4.77s |

`Azure/gpt-4.1` (non-reasoning), same task — the probe **succeeds**:

| span | `max_tokens` | error | usage_in | dur |
|---|---|---|---|---|
| probe | **1** | none | **8** | 13.49s |
| real call | — | none | 239 | 1.62s |
| real call | — | none | 270 | 5.82s |
| real call | — | none | 298 | 1.52s |

The gpt-4.1 totals reconcile exactly: 8 + 239 + 270 + 298 = 815, the value our report shows for that
task. So the probe is a real call that normally returns usage, and the failure is specific to the
reasoning model rejecting `max_tokens`.

### Impact

- **A guaranteed failed round-trip per task on reasoning models.** Every task issues a call that
  cannot succeed. Whether the `BadRequestError` is handled or merely swallowed is worth checking.
- **Call counts are inflated by one.** Anything counting `chat` spans as "LLM calls" overcounts: the
  gpt-4.1 task above made **three** real calls but reports `llm=4`.
- **Latency cost is real but smaller than the span duration suggests.** The probe is always the first
  call in a task and always the slowest (9.58s / 13.49s vs 1.5–5.8s for subsequent calls), which
  points to connection/TLS/gateway warm-up being charged to whichever call goes first. Removing the
  probe would therefore shift most of that cost onto the first real call rather than eliminate it.
  We have not isolated the two, so we are deliberately not claiming a specific saving.

### Likely cause and suggested fix

Reasoning models on the OpenAI-compatible surface reject `max_tokens` in favour of
`max_completion_tokens`. We hit the same constraint independently: a probe of ours against the same
gateway had to use `max_completion_tokens`, and with a 16-token budget `gpt-5-mini` returned
`finish_reason=length` with empty content because reasoning tokens consumed the budget.

Suggested: send `max_completion_tokens` when the model is a reasoning model (or unconditionally, if
the gateway accepts it), and consider caching the probe result per process rather than repeating it
per task.

## Contact / more data available

We have the full trace JSON for both the healthy and affected tasks (all span names, attributes and
timings), agent logs, and the exact request bodies, and can re-run any variant on demand — including
with a different model.

Bug 2's model-dependence is already confirmed here (gpt-5-mini fails, gpt-4.1 succeeds on the same
task). What we have NOT isolated is how much of the probe's wall time is intrinsic versus first-call
connection overhead — that would need a run with the probe disabled, which we cannot do from outside
the agent.
