"""The IBAC judge proxy (`deploy/helm/autobench/files/proxy.py`) — shipped by the chart, not the package.

It sits between the AuthBridge plugin and the LLM gateway, and in enforce mode a failed judge call
fails the task. On KinD 5% of the proxy's name lookups failed (2026-10-05) and the proxy made one
attempt, so one SERVFAIL cost one task. It now retries — but only failures where the request
provably never left the pod, so a slow gateway is never billed twice for one verdict.
"""

import importlib.util
import pathlib
import socket
import urllib.error

import pytest

PROXY = pathlib.Path(__file__).resolve().parent.parent / "deploy/helm/autobench/files/proxy.py"


@pytest.fixture
def proxy(monkeypatch):
    monkeypatch.setenv("UPSTREAM_BASE", "https://gateway.example.com")
    spec = importlib.util.spec_from_file_location("judge_proxy", PROXY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # the server only starts under __main__
    return mod


def flaky(errors, result="ok"):
    calls = []

    def opener(req, timeout):
        calls.append(timeout)
        if len(calls) <= len(errors):
            raise errors[len(calls) - 1]
        return result
    return opener, calls


def test_a_failed_lookup_is_retried(proxy):
    dns = urllib.error.URLError(socket.gaierror(-5, "No address associated with hostname"))
    opener, calls = flaky([dns, dns])
    assert proxy.open_with_retry("req", opener=opener, sleep=lambda s: None) == "ok"
    assert len(calls) == 3


def test_retries_are_bounded(proxy):
    dns = urllib.error.URLError(socket.gaierror(-3, "Temporary failure in name resolution"))
    opener, calls = flaky([dns] * 5)
    with pytest.raises(urllib.error.URLError):
        proxy.open_with_retry("req", opener=opener, sleep=lambda s: None)
    assert len(calls) == proxy.ATTEMPTS


@pytest.mark.parametrize("err", [
    urllib.error.URLError(TimeoutError("timed out")),        # may have been served
    TimeoutError("timed out"),
    urllib.error.HTTPError("u", 502, "Bad Gateway", {}, None),  # the gateway answered
])
def test_a_request_that_may_have_been_served_is_not_retried(proxy, err):
    opener, calls = flaky([err])
    with pytest.raises(type(err)):
        proxy.open_with_retry("req", opener=opener, sleep=lambda s: None)
    assert len(calls) == 1


def test_the_502_names_the_cause(proxy):
    e = urllib.error.URLError(socket.gaierror(-5, "x"))
    assert proxy.describe(e) == "URLError(gaierror)"
