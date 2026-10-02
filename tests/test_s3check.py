"""Covers `reference/s3check.py`, the install-time S3 gate. Not shipped in the package.

The signer is held to AWS's own published SigV4 examples, because a signer that is subtly wrong
does not fail loudly: it answers SignatureDoesNotMatch, which the gate would then report as "wrong
S3_SECRET_ACCESS_KEY" — a confident, wrong diagnosis of a key that is fine.
"""

import datetime as dt
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "reference"))

import s3check  # noqa: E402

S3_DOC_KEY = ("AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
S3_DOC_DAY = dt.datetime(2013, 5, 24, tzinfo=dt.timezone.utc)


def _sig(headers: dict) -> str:
    return headers["Authorization"].rsplit("Signature=", 1)[1]


def test_s3_doc_vector_list_objects():
    # "Example: GET Bucket (List Objects)" in the S3 SigV4 header-auth documentation.
    h = s3check.sigv4_headers(
        method="GET", host="examplebucket.s3.amazonaws.com", path="/",
        query={"max-keys": "2", "prefix": "J"}, region="us-east-1", service="s3",
        access_key_id=S3_DOC_KEY[0], secret_access_key=S3_DOC_KEY[1], now=S3_DOC_DAY)
    assert _sig(h) == "34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7"
    assert "SignedHeaders=host;x-amz-content-sha256;x-amz-date," in h["Authorization"]


def test_s3_doc_vector_get_object_with_range():
    # "Example: GET Object", which adds a signed Range header.
    h = s3check.sigv4_headers(
        method="GET", host="examplebucket.s3.amazonaws.com", path="/test.txt", query={},
        region="us-east-1", service="s3", access_key_id=S3_DOC_KEY[0],
        secret_access_key=S3_DOC_KEY[1], now=S3_DOC_DAY, extra_headers={"Range": "bytes=0-9"})
    assert _sig(h) == "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"


def test_sigv4_suite_get_vanilla():
    # The generic SigV4 test suite's get-vanilla: no x-amz-content-sha256 header.
    h = s3check.sigv4_headers(
        method="GET", host="example.amazonaws.com", path="/", query={}, region="us-east-1",
        service="service", access_key_id="AKIDEXAMPLE",
        secret_access_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        now=dt.datetime(2015, 8, 30, 12, 36, tzinfo=dt.timezone.utc), content_sha256_header=False)
    assert _sig(h) == "5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"


def _err(code: str) -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?><Error><Code>{code}</Code><Message>m</Message></Error>'


@pytest.mark.parametrize("status, body, ok", [
    (200, "<ListBucketResult/>", True),
    (403, _err("AccessDenied"), True),               # authenticated, may not list: our writer key
    (403, _err("InvalidAccessKeyId"), False),
    (403, _err("SignatureDoesNotMatch"), False),
    (404, _err("NoSuchBucket"), False),
    (400, _err("AuthorizationHeaderMalformed"), False),
    (301, _err("PermanentRedirect"), False),
    (403, _err("RequestTimeTooSkewed"), False),
    (301, "", False),
    (500, _err("InternalError"), False),             # unknown: never read as success
])
def test_classify(status, body, ok):
    assert s3check.classify(status, body)[0] is ok


def test_a_wrong_secret_is_named_as_the_secret():
    assert "S3_SECRET_ACCESS_KEY" in s3check.classify(403, _err("SignatureDoesNotMatch"))[1]


GOOD = {"S3_ENABLED": "true", "S3_BUCKET": "my-bucket", "S3_REGION": "us-east-1",
        "S3_ACCESS_KEY_ID": "AKIAIOSFODNN7EXAMPLE", "S3_SECRET_ACCESS_KEY": "s3cr3t-value"}


def _fails(env: dict) -> list[str]:
    rows, _ = s3check.check_env(env, offline=True)
    return [detail for status, _, detail in rows if status == "fail"]


def test_undeclared_is_a_failure_not_a_default():
    assert any("S3_ENABLED is unset" in d for d in _fails({}))
    assert any("exactly true or false" in d for d in _fails({"S3_ENABLED": "yes"}))


def test_declared_false_needs_nothing_else():
    rows, enabled = s3check.check_env({"S3_ENABLED": "false"}, offline=True)
    assert not enabled and all(s == "ok" for s, _, _ in rows)


def test_declared_true_names_every_missing_variable():
    (detail,) = _fails({"S3_ENABLED": "true", "S3_BUCKET": "my-bucket"})
    for name in ("S3_REGION", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"):
        assert name in detail
    assert "S3_BUCKET" not in detail


@pytest.mark.parametrize("override, needle", [
    ({"S3_BUCKET": "My_Bucket"}, "S3_BUCKET"),
    ({"S3_REGION": "US East"}, "S3_REGION"),
    ({"S3_ACCESS_KEY_ID": "short"}, "S3_ACCESS_KEY_ID"),
    ({"S3_SECRET_ACCESS_KEY": "pasted\n"}, "S3_SECRET_ACCESS_KEY"),
    ({"S3_ENDPOINT_URL": "https://minio.example.com/bucket"}, "S3_ENDPOINT_URL"),
    ({"S3_PREFIX": "/kind/"}, "S3_PREFIX"),
])
def test_shape(override, needle):
    (detail,) = _fails({**GOOD, **override})
    assert needle in detail


def test_a_good_shape_shows_the_key_id_as_a_hash_and_the_secret_not_at_all():
    rows, enabled = s3check.check_env(GOOD, offline=True)
    text = " ".join(d for _, _, d in rows)
    assert enabled and s3check.sha8(GOOD["S3_ACCESS_KEY_ID"]) in text
    assert GOOD["S3_ACCESS_KEY_ID"] not in text and GOOD["S3_SECRET_ACCESS_KEY"] not in text


@pytest.mark.parametrize("cfg, url", [
    (GOOD, "https://my-bucket.s3.us-east-1.amazonaws.com/"),
    ({**GOOD, "S3_BUCKET": "my.dotted.bucket"}, "https://s3.us-east-1.amazonaws.com/my.dotted.bucket/"),
    ({**GOOD, "S3_ENDPOINT_URL": "https://minio.example.com:9000/"},
     "https://minio.example.com:9000/my-bucket/"),
])
def test_probe_url(cfg, url):
    assert s3check.probe_url(cfg)[0] == url


def test_instance_env_round_trips_the_instance_block():
    s3 = {"bucket": "b", "region": "r", "access_key_id": "k", "secret_access_key": "s", "prefix": "p/"}
    env = s3check.instance_env(s3)
    assert (env["S3_BUCKET"], env["S3_PREFIX"], env["S3_ENDPOINT_URL"]) == ("b", "p/", "")
