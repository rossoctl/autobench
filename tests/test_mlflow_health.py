"""Tests for `GET /mlflow/health` — the install-time MLflow round-trip probe.

The stage that matters most is `write`: a broken export path is the failure that publishes a
zero-byte report on a run that reports `pass_rate 1.0`, and the exporter never raises it — it logs
it. So several tests here assert on a *logged* error becoming a response field, which is the whole
mechanism.
"""

import json
import logging

import httpx
import pytest
import respx
from starlette.testclient import TestClient

from autobench import mlflow_report
from autobench.app import create_app
from autobench.config import settings
from autobench.models import MLflowConfig
from autobench.routes import mlflow as mlflow_route

from conftest import JWKS_URL

MLFLOW_BASE = "http://mlflow.rossoctl-system.svc.cluster.local:5000"
LIST_PATH = "/api/2.0/mlflow/traces"
GET_PATH = "/api/3.0/mlflow/traces/get"


@pytest.fixture(autouse=True)
def _fast_round_trip(monkeypatch):
    """The real poll interval is 1.5 s; no test should pay for it."""
    monkeypatch.setattr(mlflow_route, "_ROUND_TRIP_INTERVAL_S", 0.001)
    monkeypatch.setattr(mlflow_route, "_ROUND_TRIP_TIMEOUT_S", 0.05)


def _client(tmp_path, instance_dict, monkeypatch):
    """A TestClient over an instance registry holding exactly `instance_dict`.

    A helper rather than only a fixture because most tests here need to edit the instance's `mlflow`
    block first, and the registry is loaded once at app startup.
    """
    (tmp_path / "kc.json").write_text(json.dumps(instance_dict))
    monkeypatch.setattr(settings, "instances_dir", str(tmp_path))
    return TestClient(create_app())


@pytest.fixture
def client(tmp_path, instance_dict, monkeypatch):
    with _client(tmp_path, instance_dict, monkeypatch) as c:
        yield c


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token(preferred_username='benchmarker')}"}


def _mock_jwks(jwks_doc):
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json=jwks_doc))


# --- credential_mode --------------------------------------------------------


@pytest.mark.parametrize(
    "cfg_kwargs,expected",
    [
        ({"bearer_token": "tok"}, "bearer_token"),
        ({"username": "u", "password": "p"}, "openshift_oauth"),
        (
            {"token_url": "http://kc/token", "client_id": "c", "client_secret": "s"},
            "client_credentials",
        ),
        ({}, "none"),
        # Precedence must match mlflow_token's, or the reported mode would describe a path the
        # Service does not actually take.
        ({"bearer_token": "tok", "username": "u", "password": "p"}, "bearer_token"),
        (
            {"username": "u", "password": "p", "token_url": "t", "client_id": "c",
             "client_secret": "s"},
            "openshift_oauth",
        ),
        # A half-filled client-credentials block is not a mode — mlflow_token would raise.
        ({"token_url": "http://kc/token", "client_id": "c"}, "none"),
    ],
)
def test_credential_mode_mirrors_mlflow_token_precedence(cfg_kwargs, expected):
    assert mlflow_route.credential_mode(MLflowConfig(**cfg_kwargs)) == expected


# --- stages -----------------------------------------------------------------


@respx.mock
def test_unconfigured_mlflow_says_so_rather_than_failing_auth(
    tmp_path, instance_dict, monkeypatch, make_token, jwks_doc
):
    """An instance with no MLflow is a supported configuration (reports fail soft)."""
    _mock_jwks(jwks_doc)
    instance_dict["mlflow"] = {}
    with _client(tmp_path, instance_dict, monkeypatch) as c:
        body = c.get("/mlflow/health", headers=_auth(make_token)).json()
    assert body["ok"] is False
    assert body["credential_mode"] == "none"
    assert "tracking_url is not configured" in body["auth"]["error"]
    assert body["write"] is None  # nothing was written


@respx.mock
def test_auth_failure_skips_the_read(client, make_token, jwks_doc):
    """No bearer means the read was never attempted — say that, don't report a read failure."""
    _mock_jwks(jwks_doc)
    body = client.get("/mlflow/health", headers=_auth(make_token)).json()
    assert body["ok"] is False
    assert body["credential_mode"] == "none"
    assert body["auth"]["ok"] is False
    assert "client-credentials not configured" in body["auth"]["error"]
    assert body["read"]["ok"] is False
    assert body["read"]["error"] == "not attempted: no bearer token"


@respx.mock
def test_read_403_is_reported_with_its_status(
    tmp_path, instance_dict, monkeypatch, make_token, jwks_doc
):
    _mock_jwks(jwks_doc)
    instance_dict["mlflow"] = {"tracking_url": MLFLOW_BASE, "bearer_token": "sa-token"}
    respx.get(url__startswith=f"{MLFLOW_BASE}{LIST_PATH}").mock(
        return_value=httpx.Response(403, json={"error_code": "PERMISSION_DENIED"})
    )
    with _client(tmp_path, instance_dict, monkeypatch) as c:
        body = c.get("/mlflow/health?round_trip=false", headers=_auth(make_token)).json()
    assert body["auth"]["ok"] is True
    assert body["credential_mode"] == "bearer_token"
    assert body["read"]["ok"] is False
    assert "HTTP 403" in body["read"]["error"]
    assert body["ok"] is False


@respx.mock
def test_round_trip_false_reads_only_and_writes_nothing(
    tmp_path, instance_dict, monkeypatch, make_token, jwks_doc
):
    _mock_jwks(jwks_doc)
    instance_dict["mlflow"] = {"tracking_url": MLFLOW_BASE, "bearer_token": "sa-token"}
    respx.get(url__startswith=f"{MLFLOW_BASE}{LIST_PATH}").mock(
        return_value=httpx.Response(200, json={"traces": []})
    )

    def _must_not_be_called(*a, **k):  # pragma: no cover - the assertion is that it is not
        raise AssertionError("round_trip=false must not emit spans")

    monkeypatch.setattr(mlflow_route, "build_tracer", _must_not_be_called)
    with _client(tmp_path, instance_dict, monkeypatch) as c:
        body = c.get("/mlflow/health?round_trip=false", headers=_auth(make_token)).json()
    assert body["ok"] is True
    assert body["read"]["ok"] is True
    assert body["write"] is None and body["round_trip"] is None


# --- the write half, which is the point -------------------------------------


class _FakeSpan:
    def __init__(self):
        self.attrs: dict = {}

    def set_attribute(self, key, value):
        self.attrs[key] = value

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeTracer:
    """Records what the probe emitted, so a test can assert on the trace's shape."""

    def __init__(self, on_flush=None):
        self.spans: list[tuple[str, _FakeSpan]] = []
        self.flushed = False
        self._on_flush = on_flush

    def start_as_current_span(self, name):
        span = _FakeSpan()
        self.spans.append((name, span))
        return span

    def flush(self):
        self.flushed = True
        if self._on_flush:
            self._on_flush()


def _install_fake_tracer(monkeypatch, on_flush=None) -> _FakeTracer:
    tracer = _FakeTracer(on_flush=on_flush)
    monkeypatch.setattr(
        mlflow_route, "build_tracer", lambda cfg, token: (tracer, tracer.flush)
    )
    return tracer


@respx.mock
def test_export_error_is_logged_not_raised_and_still_reaches_the_response(
    tmp_path, instance_dict, monkeypatch, make_token, jwks_doc
):
    """The exporter swallows failures. This is the mechanism that surfaces them anyway.

    Reproduces the real 2026-09-30 ykt5/ykt3 failure at the level the exporter actually reports it.
    A missing TLS anchor is a *retryable* error, so otel-sdk 1.44 splits it in two — the cause lands
    on WARNING, once per attempt, and the terminal ERROR is generic:

        WARNING  Transient error <cause> encountered while exporting span batch, retrying in 0.96s.
        ERROR    Failed to export span batch due to timeout, max retries or shutdown.

    Both strings below are verbatim from a live run against a self-signed endpoint. An earlier
    version of this test invented a single ERROR record that carried the cause itself, which is the
    shape a *non-retryable* 403 produces — so the test passed while the endpoint, listening on ERROR
    only, reported "timeout, max retries or shutdown" and never once said CERTIFICATE_VERIFY_FAILED.
    """
    _mock_jwks(jwks_doc)
    instance_dict["mlflow"] = {"tracking_url": MLFLOW_BASE, "bearer_token": "sa-token"}
    respx.get(url__startswith=f"{MLFLOW_BASE}{LIST_PATH}").mock(
        return_value=httpx.Response(200, json={"traces": []})
    )

    def _fail_on_flush():
        log = logging.getLogger("opentelemetry.exporter.otlp")
        for delay in ("0.96s", "2.11s", "4.46s"):
            log.warning(
                "Transient error HTTPSConnectionPool(host='mlflow.example.com', port=443): "
                "Max retries exceeded with url: /v1/traces (Caused by SSLError("
                "SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify "
                "failed: self-signed certificate (_ssl.c:1010)'))) encountered while exporting span "
                f"batch, retrying in {delay}."
            )
        log.error("Failed to export span batch due to timeout, max retries or shutdown.")

    _install_fake_tracer(monkeypatch, on_flush=_fail_on_flush)
    with _client(tmp_path, instance_dict, monkeypatch) as c:
        body = c.get("/mlflow/health", headers=_auth(make_token)).json()

    assert body["ok"] is False
    assert body["read"]["ok"] is True  # the read path answers — this is the trap
    assert body["write"]["ok"] is False
    assert "CERTIFICATE_VERIFY_FAILED" in body["write"]["error"]
    # The terminal verdict is kept too — "it gave up" and "why" are different facts, and an
    # operator needs the first to know the retries are already exhausted.
    assert "timeout, max retries or shutdown" in body["write"]["error"]
    # Reading back a trace known not to have been posted would only add a misleading failure.
    assert body["round_trip"]["ok"] is False
    assert body["round_trip"]["error"] == "not attempted: the probe spans were not exported"


@respx.mock
def test_non_retryable_export_error_names_its_status(
    tmp_path, instance_dict, monkeypatch, make_token, jwks_doc
):
    """The other real shape: a 403 is not retried, so one ERROR carries the cause and there are
    no warnings to splice. This is the RBAC half of the 2026-09-30 pair (verified live against an
    MLflow that 403s only `POST /v1/traces` while every read answers 200)."""
    _mock_jwks(jwks_doc)
    instance_dict["mlflow"] = {"tracking_url": MLFLOW_BASE, "bearer_token": "sa-token"}
    respx.get(url__startswith=f"{MLFLOW_BASE}{LIST_PATH}").mock(
        return_value=httpx.Response(200, json={"traces": []})
    )

    def _fail_on_flush():
        logging.getLogger("opentelemetry.exporter.otlp").error(
            "Failed to export span batch code: 403, reason: Forbidden"
        )

    _install_fake_tracer(monkeypatch, on_flush=_fail_on_flush)
    with _client(tmp_path, instance_dict, monkeypatch) as c:
        body = c.get("/mlflow/health", headers=_auth(make_token)).json()

    assert body["write"]["ok"] is False
    assert body["write"]["error"] == "Failed to export span batch code: 403, reason: Forbidden"
    assert "cause:" not in body["write"]["error"]  # nothing to splice, so nothing appended


@respx.mock
def test_retried_but_successful_export_is_not_a_failure(
    tmp_path, instance_dict, monkeypatch, make_token, jwks_doc
):
    """A warning with no terminal error means an attempt failed and the retry WORKED.

    Guards the regression that watching WARNING could have introduced: failing here would reject a
    healthy cluster and block an install, which is worse than the bug this endpoint exists to catch.
    The retry cause is still reported — as `detail`, not `error`.
    """
    _mock_jwks(jwks_doc)
    instance_dict["mlflow"] = {"tracking_url": MLFLOW_BASE, "bearer_token": "sa-token"}
    respx.get(url__startswith=f"{MLFLOW_BASE}{LIST_PATH}").mock(
        return_value=httpx.Response(200, json={"traces": []})
    )

    def _warn_once_on_flush():
        logging.getLogger("opentelemetry.exporter.otlp").warning(
            "Transient error 503 Service Unavailable encountered while exporting span batch, "
            "retrying in 0.96s."
        )

    _install_fake_tracer(monkeypatch, on_flush=_warn_once_on_flush)
    with _client(tmp_path, instance_dict, monkeypatch) as c:
        body = c.get("/mlflow/health", headers=_auth(make_token)).json()

    assert body["write"]["ok"] is True
    assert body["write"]["error"] is None
    assert "after a retried failure" in body["write"]["detail"]
    assert "503" in body["write"]["detail"]


@respx.mock
def test_probe_trace_is_shaped_like_a_real_run(
    tmp_path, instance_dict, monkeypatch, make_token, jwks_doc
):
    """Root `Agent.Session` + a child, both carrying session_id, and the flush happened.

    The shape is not cosmetic: `runner/tracing.py` documents that MLflow drops spans deferred to a
    single end-of-run burst, so a probe emitting only a root span could report a write failure on a
    working cluster.
    """
    _mock_jwks(jwks_doc)
    instance_dict["mlflow"] = {"tracking_url": MLFLOW_BASE, "bearer_token": "sa-token"}
    respx.get(url__startswith=f"{MLFLOW_BASE}{LIST_PATH}").mock(
        return_value=httpx.Response(200, json={"traces": []})
    )
    tracer = _install_fake_tracer(monkeypatch)
    with _client(tmp_path, instance_dict, monkeypatch) as c:
        c.get("/mlflow/health", headers=_auth(make_token))

    names = [name for name, _ in tracer.spans]
    assert names == [mlflow_report.ROOT_SPAN, "MCP.CreateSession"]
    root = tracer.spans[0][1]
    assert root.attrs["metadata.task_id"] == "mlflow-probe"
    assert root.attrs["metadata.session_id"].startswith("mlflow-probe-")
    assert tracer.spans[1][1].attrs["metadata.session_id"] == root.attrs["metadata.session_id"]
    assert tracer.flushed


def _otlp_root_span(session_id: str) -> dict:
    base = 1_000_000_000_000
    return {
        "name": mlflow_report.ROOT_SPAN,
        "span_id": "root",
        "parent_span_id": None,
        "trace_id": "tr-probe",
        "start_time_unix_nano": base,
        "end_time_unix_nano": base + 150_000_000,
        "status": {"code": "STATUS_CODE_OK"},
        "attributes": [
            {"key": "metadata.session_id", "value": {"string_value": session_id}},
            {"key": "metadata.task_id", "value": {"string_value": "mlflow-probe"}},
        ],
    }


def _mock_round_trip(tracer: _FakeTracer, *, return_the_probe: bool):
    """Answer the read-back with the probe's own trace, or with somebody else's.

    The session_id is a uuid minted inside the handler, so the mock reads it back off the fake
    tracer — which means the test exercises the real matching logic rather than a fixed string.
    """

    def _list(request):
        if not tracer.spans:
            return httpx.Response(200, json={"traces": []})
        return httpx.Response(
            200,
            json={
                "traces": [
                    {"request_id": "tr-probe", "status": "OK", "timestamp_ms": 9_999_999_999_999,
                     "execution_time_ms": 150}
                ]
            },
        )

    def _get(request):
        sid = tracer.spans[0][1].attrs["metadata.session_id"]
        if not return_the_probe:
            sid = "sess-someone-elses-run"
        return httpx.Response(200, json={"trace": {"spans": [_otlp_root_span(sid)]}})

    respx.get(url__startswith=f"{MLFLOW_BASE}{GET_PATH}").mock(side_effect=_get)
    respx.get(url__startswith=f"{MLFLOW_BASE}{LIST_PATH}").mock(side_effect=_list)


@respx.mock
def test_healthy_cluster_round_trips(
    tmp_path, instance_dict, monkeypatch, make_token, jwks_doc
):
    _mock_jwks(jwks_doc)
    instance_dict["mlflow"] = {"tracking_url": MLFLOW_BASE, "bearer_token": "sa-token"}
    tracer = _install_fake_tracer(monkeypatch)
    _mock_round_trip(tracer, return_the_probe=True)
    with _client(tmp_path, instance_dict, monkeypatch) as c:
        body = c.get("/mlflow/health", headers=_auth(make_token)).json()

    assert body["ok"] is True, body
    assert body["auth"]["ok"] and body["read"]["ok"] and body["write"]["ok"]
    assert body["round_trip"]["ok"] is True
    assert "readable after" in body["round_trip"]["detail"]


@respx.mock
def test_accepted_but_dropped_trace_fails_the_round_trip(
    tmp_path, instance_dict, monkeypatch, make_token, jwks_doc
):
    """Every POST returns 200 and the trace never materializes — the silent-drop case."""
    _mock_jwks(jwks_doc)
    instance_dict["mlflow"] = {"tracking_url": MLFLOW_BASE, "bearer_token": "sa-token"}
    tracer = _install_fake_tracer(monkeypatch)
    _mock_round_trip(tracer, return_the_probe=False)
    with _client(tmp_path, instance_dict, monkeypatch) as c:
        body = c.get("/mlflow/health", headers=_auth(make_token)).json()

    assert body["ok"] is False
    assert body["write"]["ok"] is True  # the export itself reported no error
    assert body["round_trip"]["ok"] is False
    assert "never became readable" in body["round_trip"]["error"]


# --- the probe must not be able to contaminate a report ---------------------


def test_probe_trace_cannot_enter_a_real_report():
    """Reports are filtered to the run's own session ids, so a uuid-keyed probe is excluded.

    This is what makes it safe for the probe to reuse the real `Agent.Session` span name — which it
    must, because a differently-named root span would not exercise the path a report reads.
    """
    probe = mlflow_report.transform_spans([_otlp_root_span("mlflow-probe-abc123")])
    real = mlflow_report.transform_spans([_otlp_root_span("sess-t1")])
    traces = [{"traceId": "tr-probe", "spans": probe}, {"traceId": "tr-real", "spans": real}]

    records = mlflow_report.parse_traces(traces)
    assert {r.session_id for r in records} == {"mlflow-probe-abc123", "sess-t1"}

    kept = mlflow_report.filter_by_sessions(records, ["sess-t1"])
    assert [r.session_id for r in kept] == ["sess-t1"]

    rows = mlflow_report.filter_span_rows(mlflow_report.span_rows(traces), ["sess-t1"])
    assert {r["session_id"] for r in rows} == {"sess-t1"}


# --- authorization ----------------------------------------------------------


@respx.mock
def test_non_benchmarker_is_refused(client, make_token, jwks_doc):
    """The probe writes and reads MLflow, so it is benchmarker-only like /config."""
    _mock_jwks(jwks_doc)
    resp = client.get(
        "/mlflow/health",
        headers={"Authorization": f"Bearer {make_token(preferred_username='alice')}"},
    )
    assert resp.status_code == 403
