import json

import httpx
import pytest
import respx
from starlette.testclient import TestClient

from autobench.app import create_app
from autobench.config import settings

from conftest import JWKS_URL


@pytest.fixture
def client(tmp_path, instance_dict, monkeypatch):
    (tmp_path / "kc.json").write_text(json.dumps(instance_dict))
    monkeypatch.setattr(settings, "instances_dir", str(tmp_path))
    with TestClient(create_app()) as c:
        yield c


def _mock_jwks(jwks_doc):
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json=jwks_doc))


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@respx.mock
def test_benchmarker_put_then_get_redacts_secrets(client, make_token, jwks_doc):
    _mock_jwks(jwks_doc)
    headers = _auth(make_token(preferred_username="benchmarker"))

    put = client.put(
        "/config",
        headers=headers,
        json={
            "s3": {
                "bucket": "bench-results",
                "access_key_id": "AKIA123",
                "secret_access_key": "top-secret",
            },
            "mlflow": {"tracking_url": "http://mlflow:5000", "client_secret": "hunter2"},
        },
    )
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["s3"]["bucket"] == "bench-results"
    assert body["s3"]["access_key_id"] == "AKIA123"
    assert body["s3"]["secret_access_key"] == "***"
    assert body["mlflow"]["client_secret"] == "***"

    got = client.get("/config", headers=headers)
    assert got.status_code == 200, got.text
    g = got.json()
    assert g["s3"]["bucket"] == "bench-results"
    assert g["s3"]["secret_access_key"] == "***"
    assert g["mlflow"]["client_secret"] == "***"
    assert g["mlflow"]["tracking_url"] == "http://mlflow:5000"


@respx.mock
def test_mlflow_read_fields_round_trip(client, make_token, jwks_doc):
    # The read-side MLflow fields (experiment_id/workspace/insecure_tls) are non-secret
    # and must survive a PUT/GET round-trip; only client_secret is redacted.
    _mock_jwks(jwks_doc)
    headers = _auth(make_token(preferred_username="benchmarker"))

    put = client.put(
        "/config",
        headers=headers,
        json={
            "mlflow": {
                "experiment_id": "1",
                "workspace": "team1",
                "insecure_tls": True,
                "username": "admin",
                "client_secret": "hunter2",
                "password": "pw",
                "bearer_token": "sa-jwt",
            }
        },
    )
    assert put.status_code == 200, put.text

    g = client.get("/config", headers=headers).json()["mlflow"]
    assert g["experiment_id"] == "1"
    assert g["workspace"] == "team1"
    assert g["insecure_tls"] is True
    assert g["username"] == "admin"  # non-secret, visible
    assert g["client_secret"] == "***"
    assert g["password"] == "***"
    assert g["bearer_token"] == "***"


@respx.mock
def test_partial_put_leaves_unmentioned_fields_alone(tmp_path, instance_dict, monkeypatch,
                                                     make_token, jwks_doc):
    """A PUT touching one field must not silently rewrite the others to their defaults.

    The regression this guards shipped: the merge used `exclude_none`, which drops only None
    fields, so every field with a non-None *default* was written on every call. Three qualify, and
    all three fail silently — `experiment_id` ("0"), `insecure_tls` (False) and S3's `public_read`
    (True). Observed live on ykt5 2026-09-30: a PUT of `insecure_tls` alone moved `experiment_id`
    from 1 to 0, after which the Service emitted spans under one experiment and read the report back
    from another — the empty-report signature, on a run that reports `pass_rate 1.0`.

    So the fixture must set these to NON-default values, or the bug is invisible: a reset to "0" is
    undetectable when the instance already says "0".
    """
    _mock_jwks(jwks_doc)
    instance_dict["mlflow"] = {
        "tracking_url": "https://mlflow.example.com:8443",
        "experiment_id": "7",
        "workspace": "team1",
        "insecure_tls": True,
    }
    instance_dict["s3"] = {"bucket": "bench-results", "public_read": False}
    (tmp_path / "kc.json").write_text(json.dumps(instance_dict))
    monkeypatch.setattr(settings, "instances_dir", str(tmp_path))

    with TestClient(create_app()) as c:
        headers = _auth(make_token(preferred_username="benchmarker"))
        put = c.put("/config", headers=headers, json={"mlflow": {"tracking_url": "https://new:8443"}})
        assert put.status_code == 200, put.text

        m = put.json()["mlflow"]
        assert m["tracking_url"] == "https://new:8443"  # the one field asked for
        assert m["experiment_id"] == "7"     # was silently reset to "0"
        assert m["insecure_tls"] is True     # was silently reset to False

        # The S3 section was not mentioned at all, so nothing in it may move — least of all
        # public_read, which governs the ACL on every published artifact.
        s = put.json()["s3"]
        assert s["public_read"] is False
        assert s["bucket"] == "bench-results"

        # A partial S3 PUT must likewise leave public_read where the operator put it.
        put2 = c.put("/config", headers=headers, json={"s3": {"prefix": "ykt5/"}})
        assert put2.json()["s3"]["public_read"] is False
        assert put2.json()["s3"]["prefix"] == "ykt5/"


@respx.mock
def test_explicit_null_clears_a_field(tmp_path, instance_dict, monkeypatch, make_token, jwks_doc):
    """`exclude_unset` makes an explicit null meaningful, which is the only way to unset a field.

    Under `exclude_none` a null was indistinguishable from omitting the key, so a workspace set in
    the instance file could never be cleared at runtime.
    """
    _mock_jwks(jwks_doc)
    instance_dict["mlflow"] = {
        "tracking_url": "https://mlflow.example.com:8443",
        "workspace": "team1",
        "experiment_id": "7",
    }
    (tmp_path / "kc.json").write_text(json.dumps(instance_dict))
    monkeypatch.setattr(settings, "instances_dir", str(tmp_path))

    with TestClient(create_app()) as c:
        headers = _auth(make_token(preferred_username="benchmarker"))
        body = c.put("/config", headers=headers, json={"mlflow": {"workspace": None}}).json()
        assert body["mlflow"]["workspace"] is None
        assert body["mlflow"]["experiment_id"] == "7"  # still untouched


@respx.mock
def test_non_benchmarker_forbidden(client, make_token, jwks_doc):
    _mock_jwks(jwks_doc)
    headers = _auth(make_token())  # default alice

    assert client.get("/config", headers=headers).status_code == 403
    put = client.put("/config", headers=headers, json={"s3": {"bucket": "x"}})
    assert put.status_code == 403


@respx.mock
def test_workload_key_rejected_422(client, make_token, jwks_doc):
    _mock_jwks(jwks_doc)
    headers = _auth(make_token(preferred_username="benchmarker"))
    # A workload-provided credential is not Service-enactable; extra=forbid rejects it.
    r = client.put("/config", headers=headers, json={"hf-secret": {"hf-token": "x"}})
    assert r.status_code == 422, r.text


def test_missing_token_401(client):
    assert client.get("/config").status_code == 401
    assert client.put("/config", json={}).status_code == 401


@respx.mock
def test_override_isolated_by_iss(client, make_token, jwks_doc):
    _mock_jwks(jwks_doc)
    headers = _auth(make_token(preferred_username="benchmarker"))

    client.put("/config", headers=headers, json={"s3": {"bucket": "team1-bucket"}})

    # Same instance reads back its override; the file default (no bucket) is overlaid.
    got = client.get("/config", headers=headers).json()
    assert got["s3"]["bucket"] == "team1-bucket"
    # An untouched section keeps the file default (mlflow tracking_url from instance_dict).
    assert got["mlflow"]["tracking_url"].startswith("http://mlflow")
