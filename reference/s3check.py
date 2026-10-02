#!/usr/bin/env python3
"""Is S3 declared, well-formed, and does the key authenticate? — the install-time S3 gate.

Stdlib only, on purpose: it runs in the installer's own `python3`, which has neither boto3 nor PyYAML.
Shared by both bootstrap scripts (run as a subprocess) and reference/preflight.py (imported).

    S3_ENABLED=true S3_BUCKET=... python3 reference/s3check.py            # declaration + shape + live
    python3 reference/s3check.py --offline                                # declaration + shape only

Every value is read from the ENVIRONMENT, never argv — argv is world-readable. Output is one row per
check on stderr, in the bootstrap scripts' own `ok    label` / `FAIL  label — detail` format, and the
exit status is the number of failures. The key id is only ever shown as a sha8; the secret not at all.

WHY S3 MUST BE DECLARED

Publishing is optional, but forgetting it and opting out used to look the same: the bootstrap defaulted
the bucket, wrote empty keys, and only warned. The Service gates export on the bucket alone
(routes/runs.py `_maybe_export_run`), so every run then attempted an upload that could not authenticate
and logged `S3 export failed` — a run that scores and publishes nothing, with no failure anywhere an
installer looks. So `S3_ENABLED` has no default: `false` writes no s3 block (export is skipped by
design), `true` requires every field below and proves the key.

WHY THE LIVE CHECK CAN USE A KEY THAT IS NOT ALLOWED TO DO IT

The probe is a signed `GET /?list-type=2&max-keys=0` on the bucket: it writes nothing. S3 checks the
signature BEFORE the policy, so the answer classifies the key even when it lacks s3:ListBucket — our
writer holds PutObject only:

    200, AccessDenied                          the key authenticated            -> ok
    InvalidAccessKeyId, SignatureDoesNotMatch  wrong key id / wrong secret      -> FAIL
    NoSuchBucket                               wrong bucket                     -> FAIL
    AuthorizationHeaderMalformed, 301          wrong region                     -> FAIL
    RequestTimeTooSkewed                       this machine's clock             -> FAIL

What it cannot prove is that the key may WRITE — only a write proves that, and this bucket is public.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import hmac
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

REQUIRED = ("S3_BUCKET", "S3_REGION", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY")
TEMPLATE = "reference/autobench.env.template"

BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
REGION_RE = re.compile(r"^[a-z0-9-]+$")
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

# S3 error code -> (key authenticated?, what to tell the installer)
CODES: dict[str, tuple[bool, str]] = {
    "AccessDenied": (True, "authenticated; the key may not list, which a write-only key should not"),
    "InvalidAccessKeyId": (False, "no such access key id — wrong S3_ACCESS_KEY_ID, or the key was deleted"),
    "SignatureDoesNotMatch": (False, "the key id exists but S3_SECRET_ACCESS_KEY does not match it"),
    "NoSuchBucket": (False, "no such bucket — check S3_BUCKET"),
    "AuthorizationHeaderMalformed": (False, "wrong S3_REGION for this bucket"),
    "PermanentRedirect": (False, "wrong S3_REGION for this bucket"),
    "RequestTimeTooSkewed": (False, "this machine's clock is too far off for a signed request"),
    "InvalidBucketName": (False, "S3 rejects S3_BUCKET as a bucket name"),
}


def sha8(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:8]


# --- SigV4 -------------------------------------------------------------------------------------------


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sigv4_headers(
    *,
    method: str,
    host: str,
    path: str,
    query: dict[str, str],
    region: str,
    service: str,
    access_key_id: str,
    secret_access_key: str,
    now: _dt.datetime,
    extra_headers: dict[str, str] | None = None,
    payload_sha256: str = EMPTY_SHA256,
    content_sha256_header: bool = True,
) -> dict[str, str]:
    """The headers that sign one request (AWS Signature Version 4, header-based).

    Generic over the service so it can be held to AWS's published test vectors, which use
    `service`/`us-east-1` and no `x-amz-content-sha256` header; S3 requires that header.
    """
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    day = amz_date[:8]
    headers = {"host": host, "x-amz-date": amz_date}
    if content_sha256_header:
        headers["x-amz-content-sha256"] = payload_sha256
    for k, v in (extra_headers or {}).items():
        headers[k.lower()] = v.strip()
    signed = ";".join(sorted(headers))
    canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
    canonical_query = "&".join(
        f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}"
        for k, v in sorted(query.items())
    )
    canonical_request = "\n".join(
        [method, urllib.parse.quote(path, safe="/-_.~"), canonical_query, canonical_headers, signed,
         payload_sha256]
    )
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest()]
    )
    k = _hmac(("AWS4" + secret_access_key).encode(), day)
    k = _hmac(_hmac(_hmac(k, region), service), "aws4_request")
    signature = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    out = {name: value for name, value in headers.items() if name != "host"}
    out["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access_key_id}/{scope}, SignedHeaders={signed}, "
        f"Signature={signature}"
    )
    return out


# --- the checks --------------------------------------------------------------------------------------

Row = tuple[str, str, str]  # (ok|fail|warn, label, detail)


def shape_rows(cfg: dict[str, str]) -> list[Row]:
    """Shape of an enabled S3 config: the S3_* names as keys. Empty means missing."""
    rows: list[Row] = []
    missing = [n for n in REQUIRED if not cfg.get(n)]
    if missing:
        rows.append(("fail", "s3 settings", "S3_ENABLED=true but unset: " + ", ".join(missing)))
    bad: list[str] = []
    b = cfg.get("S3_BUCKET") or ""
    if b and (not BUCKET_RE.match(b) or ".." in b):
        bad.append("S3_BUCKET is not a valid bucket name")
    r = cfg.get("S3_REGION") or ""
    if r and not REGION_RE.match(r):
        bad.append("S3_REGION is not a region name")
    kid = cfg.get("S3_ACCESS_KEY_ID") or ""
    if kid and (re.search(r"\s", kid) or not 16 <= len(kid) <= 128):
        bad.append("S3_ACCESS_KEY_ID must be 16-128 characters with no whitespace")
    sec = cfg.get("S3_SECRET_ACCESS_KEY") or ""
    if sec and re.search(r"\s", sec):
        bad.append("S3_SECRET_ACCESS_KEY contains whitespace (a pasted newline?)")
    ep = cfg.get("S3_ENDPOINT_URL") or ""
    if ep:
        u = urllib.parse.urlsplit(ep)
        if u.scheme not in ("http", "https") or not u.netloc or u.path not in ("", "/") or u.query:
            bad.append("S3_ENDPOINT_URL must be a bare http(s)://host[:port] with no path")
    p = cfg.get("S3_PREFIX") or ""
    if p.startswith("/"):
        bad.append("S3_PREFIX must not start with '/'")
    if bad:
        rows.append(("fail", "s3 settings", "; ".join(bad)))
    if not rows:
        rows.append(("ok", "s3 settings", f"bucket {b} region {r} key sha8 {sha8(kid)}"))
    return rows


def declaration_rows(env: dict[str, str]) -> tuple[list[Row], bool]:
    """(rows, enabled). `enabled` is True only for a valid `true` declaration."""
    decl = env.get("S3_ENABLED", "")
    if decl == "":
        return [(
            "fail", "s3 declared",
            f"S3_ENABLED is unset — declare S3_ENABLED=true (publish artifacts) or S3_ENABLED=false "
            f"(publish nothing). See {TEMPLATE}",
        )], False
    if decl not in ("true", "false"):
        return [("fail", "s3 declared", f"S3_ENABLED must be exactly true or false (got {decl!r})")], False
    if decl == "false":
        return [("ok", "s3 disabled by declaration", "runs score but publish no artifacts")], False
    return [("ok", "s3 declared enabled", "")], True


def probe_url(cfg: dict[str, str]) -> tuple[str, str, str]:
    """(url, host, path) for the bucket-level list request."""
    bucket, region = cfg["S3_BUCKET"], cfg["S3_REGION"]
    ep = (cfg.get("S3_ENDPOINT_URL") or "").rstrip("/")
    if ep:  # S3-compatible stores: path-style
        host = urllib.parse.urlsplit(ep).netloc
        return f"{ep}/{bucket}/", host, f"/{bucket}/"
    if "." in bucket:  # a dotted name breaks the virtual-hosted TLS wildcard
        host = f"s3.{region}.amazonaws.com"
        return f"https://{host}/{bucket}/", host, f"/{bucket}/"
    host = f"{bucket}.s3.{region}.amazonaws.com"
    return f"https://{host}/", host, "/"


def classify(status: int, body: str) -> tuple[bool, str]:
    """(authenticated, detail) from the probe's answer."""
    if status == 200:
        return True, "authenticated (200; the key may list the bucket)"
    m = re.search(r"<Code>([^<]+)</Code>", body or "")
    code = m.group(1) if m else ""
    if code in CODES:
        ok, why = CODES[code]
        return ok, f"{code}: {why}"
    if status in (301, 307) and not code:
        return False, f"HTTP {status}: wrong S3_REGION for this bucket"
    return False, f"HTTP {status}{(' ' + code) if code else ''}: unexpected answer"


def probe(cfg: dict[str, str], timeout: float = 15.0) -> Row:
    url, host, path = probe_url(cfg)
    query = {"list-type": "2", "max-keys": "0"}
    headers = sigv4_headers(
        method="GET", host=host, path=path, query=query, region=cfg["S3_REGION"], service="s3",
        access_key_id=cfg["S3_ACCESS_KEY_ID"], secret_access_key=cfg["S3_SECRET_ACCESS_KEY"],
        now=_dt.datetime.now(_dt.timezone.utc),
    )
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(query), headers=headers)
    label = f"s3 key authenticates against bucket {cfg['S3_BUCKET']}"
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, body = resp.status, resp.read(4096).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read(4096).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        return "fail", label, f"no answer from {host}: {type(reason).__name__}"
    ok, detail = classify(status, body)
    return ("ok" if ok else "fail"), label, detail


def check_env(env: dict[str, str], *, offline: bool = False) -> tuple[list[Row], bool]:
    """Every row for an S3 declaration held in `env` (os.environ or a dict). (rows, enabled)."""
    rows, enabled = declaration_rows(env)
    if not enabled:
        return rows, False
    cfg = {n: env.get(n, "") for n in (*REQUIRED, "S3_ENDPOINT_URL", "S3_PREFIX")}
    shape = shape_rows(cfg)
    rows += shape
    if offline or any(s == "fail" for s, _, _ in shape):
        return rows, True
    rows.append(probe(cfg))
    return rows, True


def instance_env(s3: dict) -> dict[str, str]:
    """An instance file's `s3` block, renamed to the S3_* names the checks take."""
    return {
        "S3_BUCKET": s3.get("bucket") or "",
        "S3_REGION": s3.get("region") or "",
        "S3_ACCESS_KEY_ID": s3.get("access_key_id") or "",
        "S3_SECRET_ACCESS_KEY": s3.get("secret_access_key") or "",
        "S3_ENDPOINT_URL": s3.get("endpoint_url") or "",
        "S3_PREFIX": s3.get("prefix") or "",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--offline", action="store_true", help="declaration and shape only; no request")
    args = ap.parse_args()
    rows, _ = check_env(dict(os.environ), offline=args.offline)
    fails = 0
    for status, label, detail in rows:
        if status == "fail":
            fails += 1
            print(f"FAIL  {label} — {detail}", file=sys.stderr)
        elif status == "warn":
            print(f"WARNING: {label} — {detail}", file=sys.stderr)
        else:
            print(f"ok    {label}{(' — ' + detail) if detail else ''}", file=sys.stderr)
    return min(fails, 100)


if __name__ == "__main__":
    sys.exit(main())
