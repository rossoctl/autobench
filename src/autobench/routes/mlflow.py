"""`GET /mlflow/health` — is this instance's MLflow reachable, authenticated and WRITABLE?

Why this exists at all, and why it writes
-----------------------------------------
MLflow is a *pre-existing* service on OpenShift (installed by rossoctl-deps / RHOAI, SAR-gated) but
normally one we install ourselves on kind (`deploy/kind/mlflow-reader.yaml`, no auth). An installer
therefore cannot assume either shape — it has to ask. And asking has to include a write, because the
failure this endpoint exists to catch is write-only: a run whose spans never reach MLflow still
reports `pass_rate 1.0` and publishes a ZERO-BYTE report, since a trace missing the Service's root
`Agent.Session` span is dropped when the report is assembled. Nothing errors; the measurement is
simply absent. Both clusters hit that on 2026-09-30 — one on a missing TLS anchor, one on a missing
RBAC grant — and in both cases the read path was answering 200 the whole time.

So the probe round-trips: mint a token, read, emit a synthetic trace through the *real* exporter,
then read it back. It reuses `auth.mlflow.mlflow_token` and `runner.tracing.build_tracer` rather than
reimplementing either, which is the point — a hand-rolled shell probe cannot exercise the Service's
own credential resolution, its `insecure_tls` handling, or its exporter session.

It costs no LLM gateway call, so it is safe to run on any cluster at any time.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid

import httpx
from fastapi import APIRouter, Depends, Request

from .. import mlflow_report
from ..auth.mlflow import MLflowAuthError, mlflow_token
from ..config import settings
from ..context import RequestContext
from ..deps import require_benchmarker
from ..models import MLflowConfig, MLflowHealthResponse, MLflowHealthStage
from ..runner.tracing import build_tracer

router = APIRouter()
logger = logging.getLogger(__name__)

# How long to wait for the probe trace to become readable. Spans leave on the BatchSpanProcessor's
# worker thread and MLflow indexes them asynchronously, so a miss on the first read is normal and
# says nothing. Bounded low: this is an interactive check, and a write that has not landed within
# this window has not landed.
_ROUND_TRIP_TIMEOUT_S = 20.0
_ROUND_TRIP_INTERVAL_S = 1.5

_PROBE_TASK_ID = "mlflow-probe"


def credential_mode(cfg: MLflowConfig) -> str:
    """Which of `mlflow_token`'s modes this config selects — mirrors its precedence exactly."""
    if cfg.bearer_token:
        return "bearer_token"
    if cfg.username and cfg.password:
        return "openshift_oauth"
    if cfg.token_url and cfg.client_id and cfg.client_secret:
        return "client_credentials"
    return "none"


class _ExportErrorCapture(logging.Handler):
    """Capture the OTLP exporter's own error records for the probe's duration.

    Necessary because the exporter never raises: `BatchSpanProcessor` runs it on a worker thread
    and an export failure is *logged* and swallowed, which is precisely why a broken write path is
    invisible during a run. Without this, the cause (`CERTIFICATE_VERIFY_FAILED`,
    `PERMISSION_DENIED`, a connect timeout) stays in the pod log and the response could only say
    "the span did not come back".

    Attached to the `opentelemetry` logger only, and detached again in a `finally` — a handler left
    on a root logger would accumulate every run's export noise.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        with contextlib.suppress(Exception):  # a diagnostic must never break what it observes
            self.messages.append(record.getMessage())

    @property
    def last(self) -> str | None:
        return self.messages[-1] if self.messages else None


@contextlib.contextmanager
def _capture_export_errors():
    handler = _ExportErrorCapture()
    otel_logger = logging.getLogger("opentelemetry")
    otel_logger.addHandler(handler)
    try:
        yield handler
    finally:
        otel_logger.removeHandler(handler)


def _emit_probe_trace(cfg: MLflowConfig, token: str, session_id: str) -> tuple[bool, str | None]:
    """Emit one probe trace through the real exporter. Returns `(exported, error)`.

    Blocking (the flush is), so the caller runs it on a thread.

    Shaped like a real run deliberately: root span named `Agent.Session` with a child that ends
    FIRST. `runner/tracing.py` documents why that ordering matters — MLflow drops spans deferred to
    a single end-of-run burst, so a probe that emitted only a root span could report a write failure
    on a cluster that works. `runner/engine.py` sets the same two `metadata.*` attributes.
    """
    built = build_tracer(cfg, token)
    if built is None:  # tracking_url unset — the caller checks this first, so this is defensive
        return False, "MLflow tracking_url is not configured"
    tracer, flush = built
    try:
        with tracer.start_as_current_span(mlflow_report.ROOT_SPAN) as root:
            root.set_attribute("metadata.session_id", session_id)
            root.set_attribute("metadata.task_id", _PROBE_TASK_ID)
            with tracer.start_as_current_span("MCP.CreateSession") as child:
                child.set_attribute("metadata.session_id", session_id)
            # MLflow's trace listing filters on duration; a trace that starts and ends inside the
            # same millisecond can read as incomplete. Cheap insurance, and it also makes the probe
            # trace visibly non-instantaneous to anyone reading the experiment.
            time.sleep(0.15)
    finally:
        flush()  # force-flushes the root span (it ends last) and shuts the exporter down
    return True, None


async def _read_one_trace_page(
    client: httpx.AsyncClient, cfg: MLflowConfig, token: str
) -> int:
    """Cheapest possible "does the traces API answer": one page of one, no span fetches.

    Deliberately NOT `download_traces` — that pages 500 at a time and then fetches the spans of
    every trace it kept, which on an experiment with a matrix in it is a long, expensive way to
    answer a yes/no question. Reuses the module-private `_mlflow_get` rather than composing the
    request here, so the `x-mlflow-workspace` header stays defined in exactly one place; a duplicate
    would drift from the report path, which is the one thing this probe must not do.
    """
    path = f"/api/2.0/mlflow/traces?experiment_ids={cfg.experiment_id}&max_results=1"
    body = await mlflow_report._mlflow_get(client, cfg, token, path)
    return len(body.get("traces", []))


async def _read_probe_traces(
    client: httpx.AsyncClient, cfg: MLflowConfig, token: str, since_ms: int
) -> list[dict]:
    """Read back recent traces, through the real report path.

    Two non-default arguments, each load-bearing:
      * `since_ms` bounds the paging to the probe's own window — without it this walks the entire
        experiment on every poll.
      * `min_duration_s=0.0` because the probe trace is ~0.15 s and the 0.1 s default sits close
        enough to it to drop it intermittently, which would read as a write failure on a healthy
        cluster.
    """
    return await mlflow_report.download_traces(
        client, cfg, token, since_ms=since_ms, min_duration_s=0.0
    )


def _contains_session(traces: list[dict], session_id: str) -> bool:
    for trace in traces:
        for span in trace.get("spans", []):
            if span.get("name") != mlflow_report.ROOT_SPAN:
                continue
            metadata = (span.get("attributes") or {}).get("metadata") or {}
            if metadata.get("session_id") == session_id:
                return True
    return False


@contextlib.asynccontextmanager
async def _mlflow_client(request: Request, cfg: MLflowConfig):
    """The same client selection the report path uses (`routes/runs.py::_fetch_records_once`).

    A self-signed in-cluster MLflow needs `verify=False`, which the shared app client does not carry
    — so probing with the shared client would fail on exactly the clusters we care about.
    """
    if cfg.insecure_tls:
        async with httpx.AsyncClient(
            timeout=settings.http_timeout_seconds, verify=False
        ) as client:
            yield client
    else:
        yield request.app.state.http


@router.get("/mlflow/health", response_model=MLflowHealthResponse)
async def mlflow_health(
    request: Request,
    ctx: RequestContext = Depends(require_benchmarker),
    round_trip: bool = True,
) -> MLflowHealthResponse:
    """Probe the caller instance's MLflow. `?round_trip=false` reads only and writes nothing.

    Always HTTP 200 — the stages carry the verdict. A probe that returned 5xx on a broken MLflow
    would be indistinguishable from a broken Service, which is the confusion this endpoint exists
    to remove.
    """
    cfg = request.app.state.config_overrides.effective(ctx.instance.iss, ctx.instance).mlflow
    mode = credential_mode(cfg)
    base = {
        "tracking_url": cfg.tracking_url,
        "credential_mode": mode,
        "experiment_id": cfg.experiment_id,
        "workspace": cfg.workspace,
        "insecure_tls": cfg.insecure_tls,
    }
    unconfigured = MLflowHealthStage(
        ok=False, error="MLflow tracking_url is not configured for this instance"
    )
    if not cfg.tracking_url:
        # Not an error state to argue with: reports fail soft by design when MLflow is absent. Say
        # so plainly rather than reporting an auth failure the operator would then chase.
        return MLflowHealthResponse(
            ok=False, auth=unconfigured, read=unconfigured, **base
        )

    async with _mlflow_client(request, cfg) as client:
        try:
            token = await mlflow_token(cfg, client)
        except (MLflowAuthError, httpx.HTTPError) as exc:
            auth = MLflowHealthStage(ok=False, error=f"{type(exc).__name__}: {exc}")
            return MLflowHealthResponse(
                ok=False,
                auth=auth,
                read=MLflowHealthStage(ok=False, error="not attempted: no bearer token"),
                **base,
            )
        auth = MLflowHealthStage(ok=True, detail=f"bearer obtained via {mode}")

        try:
            visible = await _read_one_trace_page(client, cfg, token)
        except httpx.HTTPStatusError as exc:
            read = MLflowHealthStage(
                ok=False,
                error=f"HTTP {exc.response.status_code} from {exc.request.url.path}",
            )
        except httpx.HTTPError as exc:
            read = MLflowHealthStage(ok=False, error=f"{type(exc).__name__}: {exc}")
        else:
            read = MLflowHealthStage(
                ok=True,
                detail=f"traces API answered; experiment {cfg.experiment_id} is "
                + ("non-empty" if visible else "empty (which is fine for a fresh install)"),
            )

        if not round_trip:
            return MLflowHealthResponse(ok=auth.ok and read.ok, auth=auth, read=read, **base)

        session_id = f"{_PROBE_TASK_ID}-{uuid.uuid4()}"
        # A floor for the read-back paging, taken BEFORE the write. The 60 s of slack absorbs clock
        # skew between this pod and MLflow's own timestamps.
        since_ms = int(time.time() * 1000) - 60_000
        with _capture_export_errors() as captured:
            try:
                exported, emit_error = await asyncio.to_thread(
                    _emit_probe_trace, cfg, token, session_id
                )
            except Exception as exc:  # noqa: BLE001 - a probe reports failures, it does not raise
                exported, emit_error = False, f"{type(exc).__name__}: {exc}"
            # The flush is synchronous, so any export error the exporter logged is already captured
            # by the time it returns.
            write = MLflowHealthStage(
                ok=exported and captured.last is None,
                detail="probe spans exported" if exported and not captured.last else None,
                error=emit_error or captured.last,
            )

        if not write.ok:
            # Reading back a trace we know was never posted only adds a misleading second failure.
            return MLflowHealthResponse(
                ok=False,
                auth=auth,
                read=read,
                write=write,
                round_trip=MLflowHealthStage(
                    ok=False, error="not attempted: the probe spans were not exported"
                ),
                **base,
            )

        deadline = time.monotonic() + _ROUND_TRIP_TIMEOUT_S
        found = False
        last_error: str | None = None
        attempts = 0
        while time.monotonic() < deadline and not found:
            await asyncio.sleep(_ROUND_TRIP_INTERVAL_S)
            attempts += 1
            try:
                found = _contains_session(
                    await _read_probe_traces(client, cfg, token, since_ms), session_id
                )
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
        trip = MLflowHealthStage(
            ok=found,
            detail=f"probe trace readable after {attempts} attempt(s)" if found else None,
            error=None
            if found
            else last_error
            or (
                "the probe spans were exported without error but never became readable — MLflow "
                "accepted the POST and dropped the trace (check the experiment id and workspace "
                "the Service sends against the ones the collector writes)"
            ),
        )

    return MLflowHealthResponse(
        ok=auth.ok and read.ok and write.ok and trip.ok,
        auth=auth,
        read=read,
        write=write,
        round_trip=trip,
        **base,
    )
