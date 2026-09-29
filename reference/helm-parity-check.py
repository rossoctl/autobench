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
    out = subprocess.run(
        ["helm", "template", "autobench", str(CHART), "-n", NS, *args],
        capture_output=True, text=True, check=True,
    ).stdout
    return {d["kind"]: d for d in yaml.safe_load_all(out) if d}


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


def main() -> int:
    failures: list[str] = []

    kind = render("--set", "platform=kind")
    check("kind Deployment  == deploy/deployment.yaml", load("deploy/deployment.yaml"), kind["Deployment"], failures)
    check("kind Service     == deploy/service.yaml", load("deploy/service.yaml"), kind["Service"], failures)
    check("kind HTTPRoute   == deploy/kind/httproute.yaml", load("deploy/kind/httproute.yaml"), kind["HTTPRoute"], failures)

    ocp = render("-f", str(REPO / "deploy/helm/values-ykt5.yaml"))
    patched = copy.deepcopy(load("deploy/deployment.yaml"))
    sc = patched["spec"]["template"]["spec"]["securityContext"]
    for key in ("runAsUser", "runAsGroup", "fsGroup"):  # the patch nulls these
        sc.pop(key, None)
    check("ocp  Deployment  == deployment.yaml + openshift patch", patched, ocp["Deployment"], failures)
    check("ocp  Service     == deploy/service.yaml", load("deploy/service.yaml"), ocp["Service"], failures)

    extra = sorted(k for k in ocp if k not in {"Deployment", "Service"})
    print(f"{'ok  ' if extra == ['Route'] else 'FAIL'}  ocp  extra objects == ['Route'] (got {extra})")
    if extra != ["Route"]:
        failures.append(f"unexpected extra objects: {extra}")

    if failures:
        print(f"\n{len(failures)} difference(s) — fix the CHART, not the manifests.")
        return 1
    print("\nChart and manifests agree.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
