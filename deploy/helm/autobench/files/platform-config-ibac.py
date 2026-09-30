"""Point `rossoctl-platform-config`'s ibac.* fields at AutoBench's judge, and put them back.

Run as a Helm hook Job: MODE=apply on post-install/post-upgrade, MODE=restore on pre-delete.

WHY A HOOK AND NOT A TEMPLATE. The judge Deployment/Service are AutoBench's own objects, so the
chart owns them and `helm uninstall` reclaims them. These three fields are not objects: they are keys
inside `rossoctl-platform-config`, which is TEMPLATED BY THE `rossoctl` RELEASE
(`app.kubernetes.io/managed-by: Helm`, `instance: rossoctl`). Helm owns whole objects, never fields
inside someone else's, so a template here would either fight that release or adopt its ConfigMap and
delete the platform's defaults on uninstall. A hook that patches two keys and records what they said
first is the smallest thing that gives the symmetry without claiming ownership it does not have.

Two consequences worth knowing, neither hidden:

  * `helm upgrade` of the ROSSOCTL release re-renders that ConfigMap and reverts this patch. The
    plugin then goes inert silently — every tool call admitted, the run still passes. That is why
    reference/preflight.py checks these fields before a run instead of trusting install-time state.
  * a failed restore ABORTS `helm uninstall` on purpose, rather than leaving the platform pointed at
    a judge that is about to be deleted. `helm uninstall --no-hooks` is the deliberate escape.

Stdlib only, and no yaml dependency: the document is edited LINE-WISE so the platform's own key
order and comments survive. A yaml round-trip would rewrite the whole file, which is a much larger
change than the two values asked for, and it would be invisible in a diff of the rendered chart.
"""

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

API = "https://kubernetes.default.svc"
SA = "/var/run/secrets/kubernetes.io/serviceaccount"
KEYS = ("judgeEndpoint", "judgeModel")

NS = os.environ["NAMESPACE"]
TARGET = os.environ["TARGET_CM"]
PRIOR = os.environ["PRIOR_CM"]
MODE = os.environ["MODE"]


def log(msg):
    print(f"[ibac-{MODE}] {msg}", flush=True)


def api(method, path, body=None, ctype="application/json"):
    with open(f"{SA}/token") as fh:
        token = fh.read().strip()
    ctx = ssl.create_default_context(cafile=f"{SA}/ca.crt")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path, data=data, method=method,
        headers={"Authorization": "Bearer " + token, "Accept": "application/json",
                 **({"Content-Type": ctype} if data else {})},
    )
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def cm_path(name):
    return f"/api/v1/namespaces/{NS}/configmaps/{name}"


def get_cm(name):
    status, body = api("GET", cm_path(name))
    if status == 404:
        return None
    if status != 200:
        sys.exit(f"[ibac-{MODE}] GET configmap/{name}: HTTP {status}: {body.get('message')}")
    return body


def ibac_span(lines):
    """(start, end) of the `ibac:` block's child lines. Fails rather than guessing."""
    start = None
    for i, line in enumerate(lines):
        if line.rstrip() == "ibac:":
            start = i + 1
            break
    if start is None:
        sys.exit(f"[ibac-{MODE}] configmap/{TARGET} config.yaml has no top-level `ibac:` block — "
                 "the platform's schema changed; refusing to edit blind.")
    end = start
    while end < len(lines) and (not lines[end].strip() or lines[end][:1] in (" ", "\t")):
        end += 1
    return start, end


def read_fields(doc):
    lines = doc.split("\n")
    start, end = ibac_span(lines)
    out = {}
    for line in lines[start:end]:
        key, _, val = line.partition(":")
        key = key.strip()
        if key in KEYS:
            out[key] = val.strip().strip('"').strip("'")
    missing = [k for k in KEYS if k not in out]
    if missing:
        sys.exit(f"[ibac-{MODE}] configmap/{TARGET} ibac block is missing {missing} — "
                 "the platform's schema changed; refusing to add keys it may not read.")
    return out


def write_fields(doc, values):
    lines = doc.split("\n")
    start, end = ibac_span(lines)
    for i in range(start, end):
        key, sep, _ = lines[i].partition(":")
        name = key.strip()
        if sep and name in values:
            lines[i] = f"{key}: {json.dumps(values[name])}"
    return "\n".join(lines)


def patch_target(values):
    cm = get_cm(TARGET)
    if cm is None:
        sys.exit(f"[ibac-{MODE}] configmap/{TARGET} not found in {NS} — is this a Rossoctl cluster?")
    doc = cm["data"]["config.yaml"]
    api("PATCH", cm_path(TARGET), {"data": {"config.yaml": write_fields(doc, values)}},
        ctype="application/merge-patch+json")
    after = read_fields(get_cm(TARGET)["data"]["config.yaml"])
    if after != values:
        sys.exit(f"[ibac-{MODE}] patch did not take effect: wanted {values}, read back {after}")
    log(f"configmap/{TARGET} ibac now {after}")


def apply():
    current = read_fields(get_cm(TARGET)["data"]["config.yaml"])
    desired = {"judgeEndpoint": os.environ["JUDGE_ENDPOINT"], "judgeModel": os.environ["JUDGE_MODEL"]}

    if get_cm(PRIOR) is None:
        # Record ONCE. On a re-upgrade the saved pair is already ours, and overwriting it here would
        # turn the restore into a no-op — the field would keep pointing at a judge that no longer
        # exists, which is worse than never having patched it.
        body = {"metadata": {"name": PRIOR, "labels": {"app": os.environ.get("JUDGE_APP", "ibac-judge")}},
                "data": {**current, "_savedFrom": TARGET,
                         "_savedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                         "_what": "The ibac.judgeEndpoint/judgeModel this cluster had BEFORE AutoBench "
                                  "was installed. The chart's pre-delete hook writes these back. "
                                  "Deleting this ConfigMap by hand means uninstall cannot restore."}}
        status, resp = api("POST", f"/api/v1/namespaces/{NS}/configmaps", body)
        if status not in (200, 201):
            sys.exit(f"[ibac-{MODE}] create configmap/{PRIOR}: HTTP {status}: {resp.get('message')}")
        blank = all(not v for v in current.values())
        log(f"recorded prior state in configmap/{PRIOR}: {current}"
            + ("  (empty — the judge was never configured here)" if blank else ""))
    else:
        log(f"configmap/{PRIOR} already exists; leaving the recorded prior state alone")

    if current == desired:
        log("ibac fields already as desired; nothing to patch")
        return
    patch_target(desired)


def restore():
    prior = get_cm(PRIOR)
    if prior is None:
        # Never guess. Blanking the fields could discard a value someone else set deliberately.
        log(f"configmap/{PRIOR} not found — nothing recorded, so nothing to restore. "
            f"Check configmap/{TARGET}'s ibac block by hand if this release ever patched it.")
        return
    values = {k: prior["data"].get(k, "") for k in KEYS}
    patch_target(values)
    status, resp = api("DELETE", cm_path(PRIOR))
    if status not in (200, 202, 404):
        sys.exit(f"[ibac-{MODE}] delete configmap/{PRIOR}: HTTP {status}: {resp.get('message')}")
    log(f"restored and removed configmap/{PRIOR}")


if MODE == "apply":
    apply()
elif MODE == "restore":
    restore()
else:
    sys.exit(f"MODE must be apply|restore, got {MODE!r}")
