"""Async A2A agent client, wrapping the `a2a-sdk` streaming send_message flow.

Port of the upstream `a2a_client.py` without the OTEL plumbing. `a2a-sdk` is
imported lazily so the module can be imported without the SDK present.

The agent turn is bounded by a *self-enforced* hard deadline (`timeout`). The
engine also wraps each task in `asyncio.timeout`, but that alone is not enough:
the a2a-sdk streaming `send_message` consumes from a background task group, and
cancelling the awaiting coroutine does not reliably tear down that stream — so a
stalled agent turn (e.g. its downstream MCP tool crashed) can wedge the task and,
at parallelism > 1, the whole batch. Here we run the stream in a child task and
race it with `asyncio.wait` (which always returns on its own timer, regardless of
whether the child honours cancellation); on expiry we force the httpx transport
shut so the pending stream read raises, then reap the child best-effort.

A *transport* failure is retried, a task failure is not. A mid-stream connection
drop ("peer closed connection without sending complete message body") failed one
task of a 282-task matrix — a one-off in the network, not a verdict about the
agent, and losing a task to it costs a pass rate the run then has to caveat. Each
attempt gets a fresh httpx client (the broken one's pool is unusable) and the
*remaining* share of the one deadline, so a retry never extends the turn beyond
the budget the engine already allows.
"""

import asyncio
import contextlib
import logging
import time
import uuid

logger = logging.getLogger(__name__)

# Grace period to let the child task unwind after we force the transport closed.
# If it still hasn't finished we raise anyway and let it be GC'd — the point is to
# never block the batch on an unresponsive stream.
_REAP_GRACE = 5.0

# Retry policy for transport failures. Attempts are *total*, so 3 means up to two
# retries; they share the one deadline, and we refuse to start another attempt with
# less than _RETRY_MIN_BUDGET left rather than burn the tail on a turn that cannot
# finish. Statuses: the a2a-sdk reports a dropped connection as 503, and 502/504 are
# the same class of thing from a proxy in front of the agent.
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF = 2.0
_RETRY_MIN_BUDGET = 30.0
_RETRY_STATUS = (502, 503, 504)


def _is_transport_error(exc: BaseException) -> bool:
    """True for a failure of the *connection*, not of the agent's task.

    Matched structurally, so this module still imports without the a2a-sdk: the sdk
    wraps `httpx.RequestError` as `A2AClientHTTPError(503, "Network communication
    error: ...")` and maps an upstream 5xx response through the same class, both of
    which carry `status_code`; a raw httpx transport error can also escape from code
    outside those handlers. `A2ATaskError` and `A2AAgentTimeout` match neither —
    a terminal task state is the agent's answer, and a blown deadline means the
    budget is gone.
    """
    if getattr(exc, "status_code", None) in _RETRY_STATUS:
        return True
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx is a hard dependency in practice
        return False
    return isinstance(exc, httpx.TransportError)


class A2ATaskError(RuntimeError):
    """The A2A task ended in a terminal failure state (failed/rejected/canceled)."""


class A2AAgentTimeout(RuntimeError):
    """The agent turn exceeded the client's hard deadline and was force-terminated.

    Raised out of `send_prompt` so a stalled turn fails *that task* (the engine
    records an errored result) instead of holding a slot until the run's outer
    wall-timeout — which, at parallelism > 1, would wedge the whole batch.
    """


class A2AAgentClient:
    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        timeout: float = 300.0,
        reap_grace: float = _REAP_GRACE,
        retry_attempts: int = _RETRY_ATTEMPTS,
        retry_backoff: float = _RETRY_BACKOFF,
    ):
        self._base_url = base_url
        self._token = token
        self._timeout = timeout
        self._reap_grace = reap_grace
        self._retry_attempts = max(1, retry_attempts)
        self._retry_backoff = retry_backoff

    async def send_prompt(self, prompt: str, session_id: str | None = None) -> str:
        """Drive one agent turn, retrying transport failures within the one deadline.

        The retry re-drives the *same* MCP session (a fresh one would cost a whole
        extra task), so an attempt that died after the agent had already submitted
        can submit again; the evaluator reads the session's final state, so that is
        at worst a redundant call. The failure this exists for — a connection
        truncated mid-stream — is not something the agent chose, and the alternative
        is a task lost outright.
        """
        deadline = time.monotonic() + self._timeout
        last_exc: BaseException | None = None
        for attempt in range(1, self._retry_attempts + 1):
            remaining = deadline - time.monotonic()
            if last_exc is not None and remaining < _RETRY_MIN_BUDGET:
                logger.warning(
                    "A2A agent call to %s: %.0fs left of the %g s budget after a transport "
                    "failure — not retrying",
                    self._base_url,
                    remaining,
                    self._timeout,
                )
                raise last_exc
            try:
                return await self._attempt(prompt, session_id, timeout=max(remaining, 1.0))
            except Exception as exc:  # noqa: BLE001 - re-raised unless it's transport-level
                if not _is_transport_error(exc) or attempt == self._retry_attempts:
                    raise
                last_exc = exc
                logger.warning(
                    "A2A agent call to %s failed at the transport on attempt %d/%d (%s) — "
                    "retrying in %gs",
                    self._base_url,
                    attempt,
                    self._retry_attempts,
                    exc,
                    self._retry_backoff,
                )
                await asyncio.sleep(self._retry_backoff)
        raise last_exc  # pragma: no cover - the loop always returns or raises

    async def _attempt(self, prompt: str, session_id: str | None, timeout: float) -> str:
        """One try: its own httpx client, since a failed attempt's pool is unusable."""
        import httpx

        from .tracing import inject_trace_context

        headers = {"x-session-id": uuid.uuid4().hex}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        # Nest the agent pod's own spans under the active Agent.Call span (no-op untraced).
        inject_trace_context(headers)

        httpx_client = httpx.AsyncClient(timeout=timeout, headers=headers)
        try:
            return await self._bounded_consume(httpx_client, prompt, session_id, timeout=timeout)
        finally:
            with contextlib.suppress(Exception):  # best-effort; may already be closed
                await httpx_client.aclose()

    async def _bounded_consume(
        self,
        httpx_client,
        prompt: str,
        session_id: str | None,
        timeout: float | None = None,
    ) -> str:
        """Run the stream in a child task and enforce the hard deadline independently.

        `asyncio.wait` returns on its own timer without depending on the child
        honouring cancellation, so control always comes back to us. On timeout we
        close the transport (breaking the stalled read) before cancelling+reaping.

        `timeout` is this attempt's share of the deadline — the whole of it on the
        first try, what is left of it on a retry; it defaults to the full budget.
        """
        if timeout is None:
            timeout = self._timeout
        consume = asyncio.create_task(self._consume(httpx_client, prompt, session_id))
        done, _pending = await asyncio.wait({consume}, timeout=timeout)
        if consume in done:
            return consume.result()

        with contextlib.suppress(Exception):
            await httpx_client.aclose()  # force the stalled stream read to error out
        consume.cancel()
        # Reap with asyncio.wait (not wait_for): it returns on its own timer without
        # re-awaiting the task, so even a stream that ignores cancellation cannot pin us
        # here — we surface the timeout and let the orphaned task be GC'd.
        with contextlib.suppress(BaseException):
            await asyncio.wait({consume}, timeout=self._reap_grace)
        raise A2AAgentTimeout(
            f"A2A agent call to {self._base_url} exceeded {timeout:g}s "
            "(agent turn stalled — is a downstream tool unreachable?)"
        )

    async def _consume(self, httpx_client, prompt: str, session_id: str | None) -> str:
        """The actual a2a-sdk streaming send_message flow (a testable seam)."""
        from a2a.client import ClientConfig, ClientFactory, create_text_message_object
        from a2a.client.card_resolver import A2ACardResolver
        from a2a.types import Role, TaskState, TextPart

        resolver = A2ACardResolver(httpx_client=httpx_client, base_url=self._base_url)
        card = await resolver.get_agent_card()
        # The card advertises its public URL; override to the in-cluster address we dialed.
        card.url = self._base_url
        client = ClientFactory(ClientConfig(httpx_client=httpx_client)).create(card=card)

        message = create_text_message_object(role=Role.user, content=prompt)
        request_metadata = {"session_id": session_id} if session_id else None

        result_text = ""
        final_state = None
        async for response in client.send_message(message, request_metadata=request_metadata):
            if isinstance(response, tuple):
                task, event = response
                if task.status and task.status.state:
                    final_state = task.status.state
                if event is not None and getattr(event, "artifact", None):
                    for part in event.artifact.parts:
                        if hasattr(part, "root") and isinstance(part.root, TextPart):
                            result_text += part.root.text
            elif hasattr(response, "parts"):
                for part in response.parts:
                    if hasattr(part, "root") and isinstance(part.root, TextPart):
                        result_text += part.root.text

        if final_state in (TaskState.failed, TaskState.rejected, TaskState.canceled):
            detail = result_text.strip() or "<no message>"
            raise A2ATaskError(f"A2A task ended in state '{final_state.value}': {detail}")
        return result_text
