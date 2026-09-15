"""Unit tests for the A2A client's self-enforced hard deadline and transport retry.

These exercise `_bounded_consume` directly (the `_consume` seam is overridden with
fakes) so the a2a-sdk / httpx are not required. The behaviour under test is the
tau2 #10 fix: a stalled agent turn must fail *that* call promptly instead of
wedging the batch, even when the underlying stream ignores cancellation.

Fakes model reality: a real stream read raises once the httpx transport is closed,
so a "stubborn" consume stops when `aclose()` is called (rather than leaking an
immortal task into the test event loop).

The retry tests go through `send_prompt` (the only place the retry lives), so they
override `_attempt` — one level above `_consume` — and never build an httpx client.
"""

import asyncio
import time

import httpx
import pytest

from autobench.runner.a2a_agent import (
    A2AAgentClient,
    A2AAgentTimeout,
    A2ATaskError,
    _is_transport_error,
)


class _FakeHttpx:
    def __init__(self):
        self.close_count = 0

    async def aclose(self):
        self.close_count += 1


async def test_bounded_consume_returns_result_when_fast():
    class Fast(A2AAgentClient):
        async def _consume(self, httpx_client, prompt, session_id):
            return "answer"

    c = Fast("http://agent", timeout=5)
    assert await c._bounded_consume(_FakeHttpx(), "hi", "s1") == "answer"


async def test_bounded_consume_hard_deadline_on_uncancellable_stream():
    # The stream ignores cancellation (models the a2a-sdk background task group that
    # does not tear down when the awaiting coroutine is cancelled), but — like a real
    # network read — it errors out once the transport is force-closed. The client must
    # return control promptly via its own timer and force the transport shut.
    class Stubborn(A2AAgentClient):
        async def _consume(self, httpx_client, prompt, session_id):
            while True:
                if httpx_client.close_count:
                    raise RuntimeError("stream read failed: transport closed")
                try:
                    await asyncio.sleep(0.02)
                except asyncio.CancelledError:
                    continue  # swallow cancellation; only a transport close stops us

    fake = _FakeHttpx()
    c = Stubborn("http://agent", timeout=0.2, reap_grace=0.5)
    t0 = time.monotonic()
    with pytest.raises(A2AAgentTimeout):
        await c._bounded_consume(fake, "hi", "s1")
    elapsed = time.monotonic() - t0
    assert elapsed < 3, f"wedged for {elapsed:.1f}s instead of bounding at the deadline"
    assert fake.close_count >= 1  # forced the transport shut to break the stalled read


async def test_bounded_consume_deadline_on_plain_sleep():
    # A cancellable stall (plain sleep) also fails with A2AAgentTimeout at the deadline.
    class Sleeper(A2AAgentClient):
        async def _consume(self, httpx_client, prompt, session_id):
            await asyncio.sleep(3600)

    fake = _FakeHttpx()
    c = Sleeper("http://agent", timeout=0.2, reap_grace=0.5)
    with pytest.raises(A2AAgentTimeout):
        await c._bounded_consume(fake, "hi", "s1")
    assert fake.close_count >= 1


async def test_bounded_consume_propagates_consume_error():
    # A real error inside the stream (not a timeout) surfaces to the caller unchanged.
    class Boom(A2AAgentClient):
        async def _consume(self, httpx_client, prompt, session_id):
            raise RuntimeError("agent boom")

    c = Boom("http://agent", timeout=5)
    with pytest.raises(RuntimeError, match="agent boom"):
        await c._bounded_consume(_FakeHttpx(), "hi", "s1")


async def test_concurrent_stalls_all_bound_independently():
    # The batch scenario: several stalled turns in flight must each return at the
    # deadline rather than one wedging the others (tau2 #10 at p=4).
    class Stubborn(A2AAgentClient):
        async def _consume(self, httpx_client, prompt, session_id):
            while True:
                if httpx_client.close_count:
                    raise RuntimeError("stream read failed: transport closed")
                try:
                    await asyncio.sleep(0.02)
                except asyncio.CancelledError:
                    continue

    async def one():
        c = Stubborn("http://agent", timeout=0.2, reap_grace=0.5)
        with pytest.raises(A2AAgentTimeout):
            await c._bounded_consume(_FakeHttpx(), "hi", "s")

    t0 = time.monotonic()
    await asyncio.gather(*(one() for _ in range(4)))
    assert time.monotonic() - t0 < 4  # all four drained, not serialized-and-wedged


# --- transport retry -------------------------------------------------------------
#
# The failure being fixed: one task of a 282-task matrix died on
# `HTTP Error 503: Network communication error: peer closed connection without
# sending complete message body (incomplete chunked read)` — the a2a-sdk's wrapper
# around `httpx.RequestError`, i.e. the connection, not the agent's verdict.


class _FakeSdk503(Exception):
    """Shape of `a2a.client.errors.A2AClientHTTPError` — the `status_code` attribute."""

    def __init__(self, status_code=503, message="Network communication error: peer closed"):
        self.status_code = status_code
        super().__init__(f"HTTP Error {status_code}: {message}")


class _Recording(A2AAgentClient):
    """Records each attempt's timeout budget and replays a scripted outcome list."""

    def __init__(self, *args, outcomes, **kwargs):
        super().__init__(*args, **kwargs)
        self._outcomes = list(outcomes)
        self.timeouts: list[float] = []

    async def _attempt(self, prompt, session_id, timeout):
        self.timeouts.append(timeout)
        outcome = self._outcomes.pop(0) if self._outcomes else "answer"
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


async def test_send_prompt_retries_transport_failure_then_succeeds():
    c = _Recording("http://agent", timeout=300, retry_backoff=0, outcomes=[_FakeSdk503(), "ok"])
    assert await c.send_prompt("hi", session_id="s1") == "ok"
    assert len(c.timeouts) == 2


async def test_send_prompt_surfaces_transport_failure_after_last_attempt():
    c = _Recording(
        "http://agent",
        timeout=300,
        retry_backoff=0,
        outcomes=[_FakeSdk503(), _FakeSdk503(), _FakeSdk503()],
    )
    with pytest.raises(_FakeSdk503):
        await c.send_prompt("hi")
    assert len(c.timeouts) == 3  # _RETRY_ATTEMPTS, then the error surfaces


async def test_send_prompt_does_not_retry_a_task_verdict():
    # A terminal task state IS the agent's answer — retrying it would re-run work and
    # could flip a legitimate failure. One attempt, error surfaced.
    c = _Recording(
        "http://agent",
        timeout=300,
        retry_backoff=0,
        outcomes=[A2ATaskError("A2A task ended in state 'failed': wrong answer")],
    )
    with pytest.raises(A2ATaskError):
        await c.send_prompt("hi")
    assert len(c.timeouts) == 1


async def test_send_prompt_does_not_retry_the_hard_deadline():
    c = _Recording(
        "http://agent", timeout=300, retry_backoff=0, outcomes=[A2AAgentTimeout("stalled")]
    )
    with pytest.raises(A2AAgentTimeout):
        await c.send_prompt("hi")
    assert len(c.timeouts) == 1


async def test_retries_share_one_deadline_and_never_extend_it():
    # The second attempt gets what is *left* of the budget, not a fresh one — so a
    # retried turn stays inside the ceiling the engine's per-task timeout allows.
    c = _Recording("http://agent", timeout=300, retry_backoff=0.05, outcomes=[_FakeSdk503(), "ok"])
    assert await c.send_prompt("hi") == "ok"
    first, second = c.timeouts
    assert first <= 300
    assert second < first  # shrunk by the failed attempt plus the backoff


async def test_no_retry_when_too_little_budget_remains():
    # Short-budget legs (a 10s ceiling) would spend their whole tail on a doomed
    # second attempt; below _RETRY_MIN_BUDGET we surface the transport error instead.
    c = _Recording("http://agent", timeout=10, retry_backoff=0, outcomes=[_FakeSdk503()])
    with pytest.raises(_FakeSdk503):
        await c.send_prompt("hi")
    assert len(c.timeouts) == 1


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (_FakeSdk503(), True),
        (_FakeSdk503(status_code=502), True),
        (_FakeSdk503(status_code=504), True),
        (_FakeSdk503(status_code=400), False),  # a bad request is not worth repeating
        (httpx.ReadError("peer closed"), True),  # escaped unwrapped
        (httpx.ConnectError("refused"), True),
        (A2ATaskError("failed"), False),
        (A2AAgentTimeout("stalled"), False),
        (RuntimeError("agent boom"), False),
    ],
)
def test_is_transport_error_classification(exc, expected):
    assert _is_transport_error(exc) is expected


def test_real_sdk_error_is_classified_as_transport():
    # The structural match relies on the sdk's `status_code` attribute; pin it to the
    # actual class so an sdk rename fails here rather than silently disabling retries.
    errors = pytest.importorskip("a2a.client.errors")
    exc = errors.A2AClientHTTPError(503, "Network communication error: peer closed connection")
    assert _is_transport_error(exc) is True
    assert _is_transport_error(errors.A2AClientJSONError("bad json")) is False
