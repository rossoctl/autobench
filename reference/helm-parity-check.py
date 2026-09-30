#!/usr/bin/env python3
"""Prove the Helm chart renders exactly what deploy/*.yaml produces.

The chart in deploy/helm/autobench is a generalisation of four hand-written manifests, and two
clusters are already running the manifests' output. So the chart's correctness condition is not
"it installs" but "it renders the same objects" — a stray label would change the Deployment's
immutable selector, and a dropped securityContext key would change how OpenShift admits the pod.

Run from the repo root:  python3 reference/helm-parity-check.py
Exits non-zero on any difference. Requires `helm` on PATH.

Expected differences, and the only ones tolerated: the openshift render adds a Route (nothing in
deploy/ declares one — ykt3's was created by hand), and the openshift Deployment drops
runAsUser/runAsGroup/fsGroup, which is precisely what deploy/openshift/deployment-patch.yaml's
nulls do.

The IBAC judge is off by default and so changes neither render. The last four checks cover it when
enabled: its objects are additive, it leaves the Service's alone, its two lifecycle hooks are the
ones the chart promises (create on install, restore on uninstall), and its proxy code is identical to
deploy/kind/ibac-judge.yaml's — the two install paths must not drift into serving different judges.
"""

from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
CHART = REPO / "deploy/helm/autobench"
NS = "rossoctl-system"


def render(*args: str) -> dict[str, dict]:
    """Rendered objects keyed `Kind/name`.

    Keyed by BOTH, not by kind alone: enabling the IBAC judge adds a second Deployment, a second
    Service and a second ConfigMap, and a kind-only key silently kept whichever came last — so the
    parity assertions below would have compared the judge's Deployment against deploy/deployment.yaml
    and failed for the wrong reason, or worse, passed by accident.
    """
    out = subprocess.run(
        ["helm", "template", "autobench", str(CHART), "-n", NS, *args],
        capture_output=True, text=True, check=True,
    ).stdout
    return {f"{d['kind']}/{d['metadata']['name']}": d for d in yaml.safe_load_all(out) if d}


def load(rel: str) -> dict:
    return next(d for d in yaml.safe_load_all((REPO / rel).read_text()) if d)


def diff(manifest, rendered, path="") -> list[str]:
    if type(manifest) is not type(rendered):
        return [f"{path}: {type(manifest).__name__} vs {type(rendered).__name__}"]
    out: list[str] = []
    if isinstance(manifest, dict):
        for key in sorted(set(manifest) | set(rendered)):
            if key not in manifest:
                out.append(f"{path}.{key}: only in chart render ({rendered[key]!r})")
            elif key not in rendered:
                out.append(f"{path}.{key}: only in manifest ({manifest[key]!r})")
            else:
                out += diff(manifest[key], rendered[key], f"{path}.{key}")
    elif isinstance(manifest, list):
        if len(manifest) != len(rendered):
            out.append(f"{path}: length {len(manifest)} vs {len(rendered)}")
        for i, (a, b) in enumerate(zip(manifest, rendered)):
            out += diff(a, b, f"{path}[{i}]")
    elif manifest != rendered:
        out.append(f"{path}: {manifest!r} vs {rendered!r}")
    return out


def check(label: str, manifest: dict, rendered: dict, failures: list[str]) -> None:
    deltas = diff(manifest, rendered)
    print(f"{'ok  ' if not deltas else 'FAIL'}  {label}")
    for d in deltas:
        print(f"        {d}")
    failures.extend(deltas)


def expect(got, want, label: str, failures: list[str]) -> None:
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        want {want!r}")
        print(f"        got  {got!r}")
        failures.append(f"{label}: {got!r} != {want!r}")


def main() -> int:
    failures: list[str] = []

    kind = render("--set", "platform=kind")
    check("kind Deployment  == deploy/deployment.yaml", load("deploy/deployment.yaml"), kind["Deployment/autobench-service"], failures)
    check("kind Service     == deploy/service.yaml", load("deploy/service.yaml"), kind["Service/autobench-service"], failures)
    check("kind HTTPRoute   == deploy/kind/httproute.yaml", load("deploy/kind/httproute.yaml"), kind["HTTPRoute/autobench"], failures)

    ocp = render("-f", str(REPO / "deploy/helm/values-openshift.yaml"))
    patched = copy.deepcopy(load("deploy/deployment.yaml"))
    sc = patched["spec"]["template"]["spec"]["securityContext"]
    for key in ("runAsUser", "runAsGroup", "fsGroup"):  # the patch nulls these
        sc.pop(key, None)
    check("ocp  Deployment  == deployment.yaml + openshift patch", patched, ocp["Deployment/autobench-service"], failures)
    check("ocp  Service     == deploy/service.yaml", load("deploy/service.yaml"), ocp["Service/autobench-service"], failures)

    expect(sorted(k for k in ocp if not k.endswith("/autobench-service")),
           ["Route/autobench"], "ocp  extra objects == the Route", failures)

    # The judge is off by default, so the two renders above are unaffected by it. Enabled, it must add
    # exactly its own objects and nothing else — and in particular must not disturb the Service's.
    judge = render("--set", "platform=kind", "--set", "ibacJudge.enabled=true",
                   "--set", "ibacJudge.upstreamBase=https://llm.example.com",
                   "--set", "ibacJudge.model=example/model")
    for key in ("Deployment/autobench-service", "Service/autobench-service", "HTTPRoute/autobench"):
        check(f"judge render leaves {key} unchanged", kind[key], judge[key], failures)
    expect(sorted(k for k in judge if k not in kind),
           ["ConfigMap/ibac-judge-code", "ConfigMap/ibac-judge-config-code",
            "Deployment/ibac-judge", "Job/ibac-judge-config-apply",
            "Job/ibac-judge-config-restore", "Role/ibac-judge-config",
            "RoleBinding/ibac-judge-config", "Service/ibac-judge",
            "ServiceAccount/ibac-judge-config"],
           "judge objects added", failures)

    # The two Jobs are the lifecycle contract the chart promises: one on install, one on uninstall.
    # Their cleanup policies differ on purpose — apply's Job survives success so its log stays
    # readable, restore's cannot, because after an uninstall nothing else would ever delete it.
    hooks = {k: (judge[k]["metadata"]["annotations"]["helm.sh/hook"],
                 judge[k]["metadata"]["annotations"]["helm.sh/hook-delete-policy"])
             for k in judge if k.startswith("Job/")}
    expect(hooks, {"Job/ibac-judge-config-apply": ("post-install,post-upgrade", "before-hook-creation"),
                   "Job/ibac-judge-config-restore": ("pre-delete", "before-hook-creation,hook-succeeded")},
           "judge hook + cleanup annotations", failures)

    # Byte-identical proxy code in the chart and in the raw manifest, so the two install paths cannot
    # drift into serving different judges.
    raw = next(d for d in yaml.safe_load_all((REPO / "deploy/kind/ibac-judge.yaml").read_text())
               if d["kind"] == "ConfigMap")["data"]["proxy.py"]
    expect(judge["ConfigMap/ibac-judge-code"]["data"]["proxy.py"].rstrip("\n"), raw.rstrip("\n"),
           "judge proxy.py == deploy/kind/ibac-judge.yaml's copy", failures)

    if failures:
        print(f"\n{len(failures)} difference(s) — fix the CHART, not the manifests.")
        return 1
    print("\nChart and manifests agree.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
