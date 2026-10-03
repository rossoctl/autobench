# exgentic agent — `cannot pickle '_asyncio.Task' object` fails tasks on the out-of-process runners (2026-10-01)

Reported by the AutoBench Service team. Reproduces on a stock agent image with no local patches,
**deterministically** — see the reproduction below, which turns the failure on and off by adding a
single pending task to a set.

One defect, independent of everything in
[`exgentic-agent-bug-report-20260901.md`](exgentic-agent-bug-report-20260901.md) (all three of those
are fixed and verified). It cost **13 of 166 benchmark tasks (7.8%)** in one day of matrix runs, and
every one of them is a *total* loss: the task fails before the first LLM call, so it contributes a
report row with no tokens, no duration and no pass result.

## Environment

| | |
|---|---|
| Agent image | `ghcr.io/exgentic/exgentic-a2a-tool_calling:latest` |
| Image digest | `sha256:6f70f41fa9e9871bf5cf545548e19de812e454768e0f32dbdec7506b65008914` |
| `exgentic` | `0.3.5.dev146+gff7ef6a37` |
| `pydantic` | `2.12.5` |
| `cloudpickle` | `3.1.2` |
| `a2a-sdk` | `0.3.26` |
| Runner | `EXGENTIC_DEFAULT_RUNNER=service` (we have since moved off it; see "Workaround, and a correction") |
| Agent | `tool_calling`; seen on benchmarks `gsm8k` and `appworld` |
| Model | via an OpenAI-compatible LiteLLM gateway |
| Platform | OpenShift, amd64 |

## Symptom

```
Error executing task: cannot pickle '_asyncio.Task' object
Traceback (most recent call last):
  File "/app/exgentic/src/exgentic/adapters/agents/a2a_executor.py", line 396, in execute
    await loop.run_in_executor(
  File "/app/exgentic/src/exgentic/adapters/agents/a2a_executor.py", line 399, in <lambda>
    lambda: agent_instance.start(
  File "/app/exgentic/src/exgentic/adapters/runners/transport.py", line 168, in method
    return transport.call(name, *args, **kwargs)
  File "/app/exgentic/src/exgentic/adapters/runners/service.py", line 154, in call
    "kwargs": _encode(kwargs),
  File "/app/exgentic/src/exgentic/adapters/runners/service.py", line 52, in _encode
    return base64.b64encode(cp.dumps(obj)).decode("ascii")
TypeError: cannot pickle '_asyncio.Task' object
```

The A2A task ends in state `failed` with that message as its only detail, so from the client side it
is indistinguishable from a model or gateway problem. Three tasks of one 5-task leg failed this way
within 200 ms of each other (`23:28:18.411`, `.514`, `.614`) — the first concurrent batch.

## Root cause

`start()`'s kwargs are `task=<str>`, `context={}`, `actions=cleaned_action_types`. Only the third can
hold anything unpicklable, and the chain is:

```
kwargs["actions"][i].cls                             # a dynamic pydantic subclass
  -> .__pydantic_parent_namespace__["self"]          # pydantic's ModelMetaclass captured the frame
    -> ExgenticAgentExecutor
      -> ._background_tasks                          # a set of LIVE asyncio Tasks
        -> _asyncio.Task                             # cloudpickle: TypeError
```

Two ordinary pieces of code combine into it:

1. **`_remove_session_id_from_action_types`** (`a2a_executor.py:116`) builds each action class with
   `type(f"{...}WithoutSessionId", (SingleAction,), {...})`. `SingleAction` is a pydantic model, so
   that call runs `ModelMetaclass.__new__`, which stores the **creating frame's local variables** in
   `cls.__pydantic_parent_namespace__` for forward-reference resolution. That frame is a *method*
   frame, so its locals include `self`. The resulting namespace holds all 12 locals:
   `self`, `action_type`, `action_types`, `cleaned_action_types`, `create_model`, `field_info`,
   `field_name`, `new_args_model`, `new_fields`, `original_args_model`, `ActionType`, `SingleAction`.

2. **`_fire_and_forget`** (`a2a_executor.py:167`) keeps strong references to in-flight tasks, exactly
   as the asyncio docs recommend:

   ```python
   task = asyncio.create_task(coro)
   self._background_tasks.add(task)
   task.add_done_callback(self._background_tasks.discard)
   ```

Neither is wrong by itself. Together they mean that *whenever a `_fire_and_forget` coroutine has not
yet completed*, every action class reachable from `kwargs` transitively references a live
`_asyncio.Task`, and cloudpickle — which pickles a dynamic class **by value**, `__dict__` and all —
walks straight into it.

`cleaned_action_types` is rebuilt per request (`a2a_executor.py:254`), so the capture is fresh every
time; `_background_tasks` is shared across sessions, so it is a race on *shared mutable state*.

**Note it is `.cls`, not `.arguments`.** The `create_model(...)` call on the same lines produces
`__pydantic_parent_namespace__ = None` and pickles fine — only the `type(..., (SingleAction,), ...)`
class captures the frame. A fix aimed at the wrong one of the two will look correct and change
nothing.

## Reproduction — deterministic, in the running pod

No cluster state, no LLM, no network. The only difference between the two cases is one pending task:

```python
import sys, asyncio, cloudpickle as cp
sys.path.insert(0, "/app/exgentic/src")
from exgentic.adapters.schemas.openai import openai_tools_to_action_types
from exgentic.adapters.agents.a2a_executor import ExgenticAgentExecutor

tools = [{"type": "function", "function": {"name": "calculate", "description": "m",
          "parameters": {"type": "object",
                         "properties": {"session_id": {"type": "string"}, "expr": {"type": "string"}},
                         "required": ["session_id", "expr"]}}}]
ats = openai_tools_to_action_types(tools)
clean = ExgenticAgentExecutor._remove_session_id_from_action_types

class Fake:                                  # stands in for the executor
    def __init__(self): self._background_tasks = set()

async def main():
    ex = Fake()
    async def slow(): await asyncio.sleep(5)
    for label in ("empty set", "1 pending task"):
        acts = clean(ex, ats)
        try:
            cp.dumps({"task": "q", "context": {}, "actions": acts})
            print(f"  {label:15}: kwargs pickle OK")
        except TypeError as e:
            print(f"  {label:15}: FAILS: {e}")
        ex._background_tasks.add(asyncio.create_task(slow()))

asyncio.run(main())
```

```
  empty set      : kwargs pickle OK
  1 pending task : FAILS: cannot pickle '_asyncio.Task' object
```

And the chain is confirmed by removing *only* the captured namespace — the same object then pickles:

```python
cls = clean(ex, ats)[0].cls
cls.__pydantic_parent_namespace__ = None
cp.dumps(cls)                                # OK
```

## Why it is intermittent, and what widens the window

The executor emits progress events through `_fire_and_forget` **before** the `start()` encode —
`a2a_executor.py:330` fires `emit_event("✓ Using session_id …")` on the same request path that
reaches the encode at line 398. So a single session can lose the race against *its own* emit; it does
not require concurrency, it is merely far likelier with it.

Measured incidence over 166 tasks in 17 legs on one cluster:

| leg | tasks | parallel | pickle failures |
|---|---|---|---|
| appworld, 20 tasks, no sidecar | 20 | 4 | **3** |
| gsm8k `ibac-only` (run 2) | 5 | 4 | **3** |
| gsm8k `ibac-only` (run 3) | 5 | 4 | **3** |
| gsm8k `full` | 5 | 4 | **2** |
| gsm8k `auth-only` | 5 | 4 | **1** |
| gsm8k `full` + observe | 5 | 4 | **1** |
| gsm8k, no plugins (1, 5, 10 and **50** tasks) | 66 | 1 and 4 | 0 |
| tau2 (10 and 20 tasks) | 30 | 1 and 4 | 0 |
| the other 5 plugin legs | 25 | 4 | 0 |

Two things visibly widen the window, and both are about *how long the encode and the emit overlap*,
which is consistent with the mechanism:

- **A sidecar on the event path.** Our plugin legs run an outbound-interception proxy beside the
  agent, which slows `emit_event`, so its task is still pending when the next session encodes: 10 of
  50 tasks across the plugin legs, against **0 of 66** on the same benchmark and same parallelism
  without it.
- **A large tool count.** appworld exposes hundreds of tools, so the clean-and-pickle pass over the
  action types takes far longer than gsm8k's single tool — 3 of 20 with no sidecar at all, while
  gsm8k at **50 tasks and the same 4-way parallelism lost nothing**.

So neither concurrency nor the sidecar is the cause; they are amplifiers. The 50-task leg is the
useful control — it rules out "more tasks, more failures".

## Impact

The task fails *before* the agent instance starts, so:

- **it is a total loss, not a degraded result** — no LLM call, no tokens, no duration;
- **it lands in the report as a row of nulls**, which skews every per-task aggregate computed over
  the leg unless the consumer excludes it explicitly;
- **it is silent on the success path** — the A2A state is `failed` with a `TypeError` string, so it
  reads as a flaky model or gateway rather than a structural defect;
- **it scales with the tool count**, so the benchmarks that cost the most to run are the ones that
  lose the most tasks.

## Suggested fix, in preference order

1. **Drop the captured namespace on the classes built for transport.** One line after the `type(...)`
   call in `_remove_session_id_from_action_types`:

   ```python
   new_action_cls.__pydantic_parent_namespace__ = None
   ```

   The namespace exists to resolve forward references; these classes are built from fully-resolved
   annotations, so nothing needs it. Verified above to be sufficient. Cheapest, and it fixes the
   whole class of problem — any future unpicklable attribute on the executor is also excluded.

2. **Build the class out of method scope**, e.g. with `create_model(..., __base__=SingleAction)` or
   from a module-level helper, so the captured frame has no `self` in it. Equivalent effect, slightly
   larger change; note `create_model` already produces `None` here, which is why `.arguments` is not
   affected.

3. **Make `_encode` fail loudly about *what* it could not pickle.** Independent of the above, and
   worth it on its own: `cloudpickle`'s message names the leaf type and nothing about the path, which
   is why this took a day to localise. Naming the top-level kwarg would have been enough.

Not recommended: holding `_background_tasks` as a `WeakSet` or moving it off `self`. It would fix this
instance by accident while leaving the frame capture in place, so the next unpicklable attribute
reintroduces the bug.

## Workaround, and a correction

**Correction (2026-10-03).** This section first said we could not work around the defect, because
only `service` produced the OTEL spans our token telemetry is built from. **That was wrong for this
image.** We had not re-measured it on dev146.

**`direct` emits complete, correctly parented spans, and it is now our runner.** A controlled A/B on
kind: gsm8k, 10 tasks, `max_parallel_sessions: 4`, this image, only `EXGENTIC_DEFAULT_RUNNER` changed.
- **`direct`:** 10 of 10 `chat` spans landed in their own task's trace.
- **`service`:** only 7 arrived, and 6 of those were parented to the wrong session.

That is a second defect, on the same runner:
- **What happens.** `service` hosts the agent behind server threads that inherit no ContextVar, so
  `TraceLogger._get_parent_context` falls back to `_SUBPROCESS_CONTEXT`.
- **Why it misattributes.** `a2a_executor.py` overwrites that fallback at the start of every request,
  bare at :222 and with the session at :371. So under concurrency, a span is parented to whichever
  session started last.
- **Why spans go missing.** A just-started session's bare context has no `otel_context`, so the span
  becomes an orphan trace with no session root.
- **Why `direct` is clean.** The `contextvars.copy_context()` / `ctx.run` handoff at ~:384 already
  gives each executor thread the right context, and `direct` calls the agent on those threads.

The pickling fix above still matters upstream: exgentic's own default runner is `venv`, which pickles
too.

- **The `direct` and `thread` runners are immune** (they never pickle; only `service`, `process`,
  `docker` and the `venv` transport do).
- **`max_parallel_sessions: 1` only reduces the rate**, since the same-session emit at line 330 races
  the encode regardless — and it multiplies wall-clock time for every leg.
- **Retrying the task** would work, but it changes what the benchmark measures: a retried task is no
  longer one trial.

## Contact / more data available

Raised by the AutoBench Service team. We can supply the full agent log, the per-task report rows for
all 13 failures, and the pod's package manifest on request.
