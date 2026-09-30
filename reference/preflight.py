#!/usr/bin/env python3
"""Preflight an AutoBench install — read-only, no cluster writes, no secret values printed.

Run this BEFORE `helm upgrade --install` (and again after, as a post-install audit). Every check
here corresponds to a failure we have actually shipped, and the reason there is a script at all is
that most of those failures do not look like misconfiguration:

  * an empty `openai-secret` 401s per completion, mid-run, and the leg finishes with zeroes
  * a missing `hf-secret` puts the MCP pod in CreateContainerConfigError, and the agent then
    crash-loops against it — surfacing as a 424 on the run, several layers away from the cause
  * the collector's HTTP receiver is on :8335 by a command-line override; its ConfigMap still
    says 4318, and an agent pointed at 4318 hard-fails into CrashLoopBackOff
  * an MLflow the Service reads but the collector does not write to (or a mismatched experiment
    id) produces a run that PASSES with `model: "unknown"` and every token count 0
  * Rossoctl below v0.8.0 accepts the Service's deploy request and silently drops fields it does
    not know, so the benchmark comes up in a shape nobody configured

Nothing here is inferred from a manifest when it can be read from the live object: the collector's
port comes from the Deployment's `command`, not from its ConfigMap, and the MLflow target and
experiment id come from the collector's own exporter rather than from anyone's intent.

Secrets are read (they have to be — "is it empty?" is the check) but never printed: every value is
reported as an 8-char SHA-256 prefix, unconditionally, with no attempt to decide which values are
sensitive. Two hashes matching is the strongest thing this script will say about a credential.

Usage
-----
    python3 reference/preflight.py                                  # infer platform from context
    python3 reference/preflight.py --platform kind  --context kind-rossoctl
    python3 reference/preflight.py --platform openshift --context <ctx>
    python3 reference/preflight.py --json                           # machine-readable

Exit status is 0 when nothing FAILed (warnings do not fail the run), 1 otherwise.

The `benchmarker` credential is checked, not assumed
----------------------------------------------------
Every `/deploy` the Service makes is a ROPC login as that user, so an absent, stale or
required-action-blocked password does not degrade the install — it makes every run fail with a 502
wrapping a 403. The password is taken from, in order: `--password-file` (a chmod-600 file — the
preferred form, because the value never reaches argv), `--password-stdin`, `--password` (accepted,
but argv is world-readable), `$KC_SERVICE_PASSWORD`, and finally the `service_credential` inside the
instance Secret. That last source is the most useful one: it is the copy the Service actually
presents, so checking it says something about the install rather than about your shell. When a
password is supplied *and* the Secret holds one, a mismatch is a FAILURE — otherwise a green
preflight can sit next to a Service that 502s on every deploy.

    python3 reference/preflight.py --password-file ~/.rossoctl-kind/benchmarker.pass

Two things ROPC cannot distinguish are "the password is wrong" and "the user has no password
credential at all" — Keycloak answers `invalid_grant` to both. With a Keycloak admin credential
(`KC_ADMIN_PASSWORD`, or an in-cluster `keycloak-initial-admin` Secret, which is how KinD ships) the
Admin REST API is asked directly: does the user exist, is it enabled, is a `password` credential
set, are `firstName`/`lastName` present, is a required action pending. Without one that tier is
skipped with a note and the ROPC login stands as the behavioural check.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

OK, FAIL, WARN, SKIP = "ok", "FAIL", "warn", "skip"
_ICON = {OK: "ok   ", FAIL: "FAIL ", WARN: "warn ", SKIP: "skip "}

DEFAULT_NS = "rossoctl-system"
# Only the namespaces you actually deploy into. Every spec in reference/run12_specs.json names
# `team1` (24 of 24), so a second namespace is opt-in via --teams rather than assumed: reporting an
# unused `team2` as a FAILURE buries the rows that matter under noise nobody will act on.
DEFAULT_TEAMS = ("team1",)

# The LLM gateway profile, mirroring reference/llm-profiles.sh. Kept in step with it by hand — both
# are small, and importing shell into python is worse than duplicating four lines.
LLM_PROFILE_ENV_FILE = os.environ.get(
    "LLM_PROFILE_ENV_FILE", os.path.expanduser("~/.rossoctl-llm/profiles.env")
)


def llm_profile_base(profile: str) -> str | None:
    """The api_base the named profile declares, from the environment or profiles.env."""
    if profile not in ("intranet", "internet"):
        return None
    name = f"{profile.upper()}_LLM_API_BASE"
    if os.environ.get(name):
        return os.environ[name]
    try:
        with open(LLM_PROFILE_ENV_FILE, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(f"{name}="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return None


def llm_profile_from_values(path: str) -> str | None:
    """The `llmProfile:` declared in a chart values file, so one file is the source of truth."""
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("llmProfile:"):
                    return line.split(":", 1)[1].strip().strip('"') or None
    except OSError:
        return None
    return None
COLLECTOR_DEPLOY = "otel-collector"
MLFLOW_EXPORTER = "otlphttp/mlflow"
INSTANCES_SECRET = "autobench-instances"
SERVICE_DEPLOY = "autobench-service"
CHART_DIR = "deploy/helm/autobench"
MIN_HELM = (3, 8)


def sha8(value: str | bytes) -> str:
    raw = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()[:8]


class Report:
    """Collects results and prints them as they happen — a slow check should not look like a hang."""

    def __init__(self, quiet: bool = False) -> None:
        self.rows: list[dict] = []
        self.quiet = quiet

    def add(self, status: str, label: str, detail: str = "") -> None:
        self.rows.append({"status": status, "label": label, "detail": detail})
        if not self.quiet:
            line = f"  {_ICON[status]} {label}"
            if detail:
                line += f" — {detail}"
            print(line, flush=True)

    def ok(self, label: str, detail: str = "") -> None:
        self.add(OK, label, detail)

    def fail(self, label: str, detail: str = "") -> None:
        self.add(FAIL, label, detail)

    def warn(self, label: str, detail: str = "") -> None:
        self.add(WARN, label, detail)

    def skip(self, label: str, detail: str = "") -> None:
        self.add(SKIP, label, detail)

    def section(self, title: str) -> None:
        if not self.quiet:
            print(f"\n{title}", flush=True)

    def count(self, status: str) -> int:
        return sum(1 for r in self.rows if r["status"] == status)


# --- cluster access ---------------------------------------------------------


class Cluster:
    def __init__(self, context: str | None, binary: str = "kubectl") -> None:
        self.context = context
        self.binary = binary

    def run(self, *args: str, timeout: int = 60) -> tuple[int, str, str]:
        cmd = [self.binary]
        if self.context:
            cmd += ["--context", self.context]
        cmd += list(args)
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, "", f"timed out after {timeout}s"
        except FileNotFoundError:
            return 127, "", f"{self.binary} not found"
        return p.returncode, p.stdout.strip(), p.stderr.strip()

    def get_json(self, *args: str, timeout: int = 60) -> dict | None:
        rc, out, _ = self.run(*args, "-o", "json", timeout=timeout)
        if rc != 0 or not out:
            return None
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return None

    def exists(self, *args: str) -> bool:
        return self.run(*args, timeout=30)[0] == 0

    def api_resource(self, group: str, resource: str) -> bool:
        """Is <resource>.<group> served? Not the same question as "is there a CRD".

        OpenShift's Route is served by the aggregated openshift-apiserver, so it has no CRD at all
        and `get crd routes.route.openshift.io` reports it missing on a cluster that plainly has
        Routes. `api-resources` answers for both kinds of API.
        """
        rc, out, _ = self.run("api-resources", f"--api-group={group}", "-o", "name", timeout=45)
        return rc == 0 and any(ln.strip().startswith(f"{resource}.") for ln in out.splitlines())

    def secret_data(self, namespace: str, name: str) -> dict[str, bytes] | None:
        """Decoded Secret data. Values are returned for hashing/parsing, never for printing."""
        obj = self.get_json("-n", namespace, "get", "secret", name)
        if obj is None:
            return None
        return {k: base64.b64decode(v) for k, v in (obj.get("data") or {}).items()}


# --- individual check groups ------------------------------------------------


def check_tooling(rep: Report, platform: str, cluster: Cluster) -> None:
    rep.section("Tooling")
    for tool, required in (("kubectl", True), ("jq", True), ("curl", True), ("helm", True)):
        path = shutil.which(tool)
        if path:
            rep.ok(f"{tool} present")
        elif required:
            rep.fail(f"{tool} present", "not on PATH")
    if platform == "kind":
        for tool in ("kind",):
            (rep.ok if shutil.which(tool) else rep.warn)(
                f"{tool} present", "" if shutil.which(tool) else "needed to (re)build and load the image"
            )
        if not (shutil.which("docker") or shutil.which("podman")):
            rep.warn("container engine present", "neither docker nor podman on PATH")
        else:
            rep.ok("container engine present")

    p = subprocess.run(["helm", "version", "--short"], capture_output=True, text=True)
    if p.returncode != 0:
        rep.fail("helm >= 3.8", "helm version failed")
        return
    m = re.search(r"v(\d+)\.(\d+)", p.stdout)
    if not m:
        rep.warn("helm >= 3.8", f"could not parse {p.stdout.strip()!r}")
    elif (int(m.group(1)), int(m.group(2))) >= MIN_HELM:
        rep.ok(f"helm >= 3.8 (found {m.group(0)})")
    else:
        rep.fail("helm >= 3.8", f"found {m.group(0)}; `--kube-context` and OCI support need 3.8+")


def check_cluster(rep: Report, cluster: Cluster) -> bool:
    rep.section(f"Cluster ({cluster.context or 'current context'})")
    rc, out, err = cluster.run("get", "nodes", "--no-headers", timeout=45)
    if rc != 0:
        rep.fail("cluster reachable", err.splitlines()[0] if err else f"kubectl exited {rc}")
        return False
    lines = [ln for ln in out.splitlines() if ln.strip()]
    ready = [ln for ln in lines if re.search(r"\bReady\b", ln)]
    if not lines:
        rep.fail("cluster reachable", "no nodes returned")
        return False
    if len(ready) == len(lines):
        rep.ok(f"cluster reachable ({len(lines)} node(s) Ready)")
    else:
        rep.fail("all nodes Ready", f"{len(ready)}/{len(lines)} Ready")
    return True


def _version_at_least_080(found: str) -> bool:
    """0.8.0-rc.2 counts as 0.8.0 — a release candidate of the required minor is the required minor."""
    base = found.split("-", 1)[0]
    parts = base.split(".")
    try:
        major, minor = int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return False
    return major > 0 or minor >= 8


def check_rossoctl(rep: Report, cluster: Cluster, namespace: str) -> None:
    rep.section("Rossoctl platform")
    # The BACKEND's chart label, deliberately: the operator subchart carries its own lower version
    # line (0.4.x) and comparing that one rejects a perfectly current cluster.
    rc, version, _ = cluster.run(
        "-n", namespace, "get", "deploy", "rossoctl-backend",
        "-o", r"jsonpath={.metadata.labels.app\.kubernetes\.io/version}",
    )
    if rc != 0 or not version:
        rep.fail(
            "Rossoctl >= 0.8.0",
            f"no app.kubernetes.io/version on deploy/rossoctl-backend in {namespace}",
        )
    elif _version_at_least_080(version):
        rep.ok(f"Rossoctl >= 0.8.0 (found {version})")
    else:
        rep.fail("Rossoctl >= 0.8.0", f"found {version} — upgrade before installing AutoBench")

    for crd in ("agentruntimes.agent.rossoctl.dev", "agentcards.agent.rossoctl.dev"):
        if cluster.exists("get", "crd", crd):
            rep.ok(f"CRD {crd}")
        else:
            rep.fail(f"CRD {crd}", "not installed — the operator cannot accept a deploy")

    # Informational, and the reason the plugin legs of the matrix are OpenShift-only: stock KinD's
    # agentruntimes CRD has no pluginPreset field, so a preset is accepted and dropped.
    rc, schema, _ = cluster.run(
        "get", "crd", "agentruntimes.agent.rossoctl.dev",
        "-o", "jsonpath={.spec.versions[*].schema.openAPIV3Schema}",
    )
    if rc == 0 and schema:
        if "pluginPreset" in schema:
            rep.ok("agentruntimes CRD supports pluginPreset (AuthBridge plugin legs available)")
        else:
            rep.warn(
                "agentruntimes CRD has no pluginPreset field",
                "plugin legs will be accepted and silently ignored on this cluster",
            )


def check_namespaces(
    rep: Report, cluster: Cluster, namespace: str, teams: list[str], workload: Cluster | None
) -> None:
    rep.section("Namespaces")
    if cluster.exists("get", "ns", namespace):
        rep.ok(f"namespace {namespace} (Service)")
    else:
        rep.fail(f"namespace {namespace} (Service)", "absent")
    if workload is None:
        rep.skip("team namespaces", "they live on the workload cluster — pass --workload-context")
        return
    for ns in teams:
        if workload.exists("get", "ns", ns):
            rep.ok(f"namespace {ns} (workload)")
        else:
            rep.fail(f"namespace {ns} (workload)", "absent")


def check_workload_secrets(rep: Report, cluster: Cluster | None, teams: list[str]) -> None:
    rep.section("Workload secrets (per team namespace)")
    if cluster is None:
        # ykt3's shape: the Service runs on one cluster and every agent/MCP pod on another, so its
        # own team namespaces hold nothing and checking them there would fail on all three counts.
        rep.skip(
            "openai-secret / hf-secret",
            "the workloads are on another cluster — re-run with --workload-context <ctx> to check them",
        )
        return
    for ns in teams:
        data = cluster.secret_data(ns, "openai-secret")
        if data is None:
            rep.fail(f"{ns}/openai-secret", "absent — every completion will 401")
        elif not data.get("apikey"):
            rep.fail(f"{ns}/openai-secret apikey", "present but EMPTY — every completion will 401")
        else:
            rep.ok(f"{ns}/openai-secret apikey", f"sha8 {sha8(data['apikey'])}")

    for ns in teams:
        data = cluster.secret_data(ns, "hf-secret")
        if data is None:
            rep.fail(
                f"{ns}/hf-secret",
                "absent — the MCP pod stays in CreateContainerConfigError and the agent crash-loops",
            )
        elif "hf-token" not in data:
            rep.fail(f"{ns}/hf-secret", "exists but has no hf-token key")
        else:
            # Presence is the requirement; gsm8k's dataset is public, so an empty value is fine.
            detail = f"sha8 {sha8(data['hf-token'])}" if data["hf-token"] else "empty value (fine)"
            rep.ok(f"{ns}/hf-secret hf-token", detail)

    # Cross-namespace consistency, when --teams names more than one: they must hold the SAME gateway
    # key, or a leg that lands in the second namespace fails while the identical leg in the first
    # passes — and nothing in the run output says which namespace it used.
    keys = {}
    for ns in teams:
        data = cluster.secret_data(ns, "openai-secret") or {}
        if data.get("apikey"):
            keys[ns] = sha8(data["apikey"])
    if len(keys) > 1:
        if len(set(keys.values())) == 1:
            rep.ok("the team namespaces share one LLM key", f"sha8 {next(iter(keys.values()))}")
        else:
            rep.warn(
                "the team namespaces hold DIFFERENT LLM keys",
                ", ".join(f"{ns}={h}" for ns, h in keys.items()),
            )


def check_ibac_judge(rep: Report, cluster: Cluster, namespace: str, *, required: bool) -> None:
    """The IBAC judge: AutoBench-owned, and silent when missing — which is why this check exists.

    The judge is AutoBench's. Rossoctl ships no judge — `rossoctl-platform-config` arrives with every
    `ibac.*` field EMPTY — and a cluster with no benchmark workloads never needs one, so the judge on
    these clusters was created for the plugin legs and for nothing else. The chart therefore INSTALLS
    it (`ibacJudge.enabled`) and `helm uninstall` removes it again, which is the only lifecycle that
    leaves nothing behind for the next person to puzzle over.

    The two `ibac.*` FIELDS are the one part that cannot be chart-owned: they are keys inside a
    ConfigMap templated by the `rossoctl` release, and Helm owns whole objects, not fields inside
    another release's. So a post-install hook records what they said and patches them, and a
    pre-delete hook writes the recorded pair back — see the chart's files/platform-config-ibac.py.

    Which is exactly why this check is not redundant with the install. A `helm upgrade` of the
    ROSSOCTL release re-renders that ConfigMap and reverts the patch, and nothing about that failure
    is loud: an unset judgeEndpoint fails no deploy and no run, plugin legs pass with the ibac plugin
    inert, and the only tell is a judge call count of zero. That is how it went unnoticed once
    already. Install-time state is not run-time state; this reads the live cluster.

    `required` is the caller saying plugin legs are in scope; otherwise a missing judge is a warning,
    because legs #1-#4 and #9-#12 neither use nor need one.
    """
    rep.section("IBAC judge (needed only by the plugin legs, #5-#8)")
    note = rep.fail if required else rep.warn

    cm = cluster.get_json("get", "cm", "rossoctl-platform-config", "-n", namespace, "-o", "json")
    ibac: dict = {}
    if cm:
        raw = (cm.get("data") or {}).get("config.yaml") or ""
        # Deliberately not a YAML parse: preflight has no yaml dependency, and the three keys are
        # flat scalars two spaces under `ibac:`. Anything more structured belongs to the operator.
        in_ibac = False
        for line in raw.splitlines():
            if line.startswith("ibac:"):
                in_ibac = True
                continue
            if in_ibac:
                if line and not line.startswith((" ", "\t")):
                    break
                if ":" in line:
                    k, _, v = line.strip().partition(":")
                    ibac[k.strip()] = v.strip().strip('"').strip("'")
    else:
        note("rossoctl-platform-config", f"not readable in {namespace} — cannot tell if a judge is configured")
        return

    endpoint, model = ibac.get("judgeEndpoint", ""), ibac.get("judgeModel", "")
    if not endpoint and not model:
        note("ibac.judgeEndpoint / judgeModel",
             "both EMPTY — the ibac plugin loads and does nothing. Legs #5-#8 would complete and "
             "measure no enforcement at all; the tell is zero judge calls, not an error")
    else:
        if endpoint:
            rep.ok("ibac.judgeEndpoint", "set")
        else:
            note("ibac.judgeEndpoint", "empty while judgeModel is set — the plugin cannot call anything")
        if model:
            rep.ok("ibac.judgeModel", model)
        else:
            note("ibac.judgeModel", "empty while judgeEndpoint is set — the judge has no model to use")
    if ibac.get("judgeBearer"):
        # Cleartext in a ConfigMap. The working shape is a Secret-backed judge proxy.
        rep.warn("ibac.judgeBearer is set", "a ConfigMap is cleartext — use the Secret-backed proxy instead")

    # The judge's upstream key is its own, and its model must come from THAT gateway's catalogue.
    # Same pairing as the workload profile's base+model, and it fails the same silent way.
    if cluster.secret_data(namespace, "ibac-judge-upstream") is None:
        note(f"{namespace}/ibac-judge-upstream",
             "absent — the judge proxy has no upstream key, so every judge call 401s")
    else:
        rep.ok(f"{namespace}/ibac-judge-upstream", "present")
    if cluster.exists("get", "deploy", "ibac-judge", "-n", namespace):
        rep.ok("ibac-judge Deployment", "present")
    else:
        note("ibac-judge Deployment",
             f"absent from {namespace} — install it with the chart: "
             "helm upgrade --install ... --set ibacJudge.enabled=true "
             "--set ibacJudge.upstreamBase=<that gateway> --set ibacJudge.model=<one of its models>")

    # Present only between a chart install and its uninstall, and it is what makes the uninstall able
    # to put the platform's own values back. Worth reporting because deleting it by hand is silent:
    # nothing fails, and uninstall then leaves the fields pointing at a judge that no longer exists.
    if cluster.exists("get", "cm", "ibac-judge-prior", "-n", namespace):
        rep.ok("ibac-judge-prior", "present — uninstall can restore the platform's original ibac fields")
    elif endpoint:
        rep.warn("ibac-judge-prior",
                 f"absent from {namespace} while ibac.judgeEndpoint is set — either the judge was "
                 "configured outside the chart, or the record was deleted. `helm uninstall` will "
                 "leave those fields as they are rather than guess")


def check_collector(rep: Report, cluster: Cluster, namespace: str) -> dict:
    """Returns what the collector actually does: {http_port, traces_endpoint, experiment_id, workspace}."""
    rep.section("OTEL collector (the write half of the telemetry chain)")
    info: dict = {}
    rc, command, _ = cluster.run(
        "-n", namespace, "get", "deploy", COLLECTOR_DEPLOY,
        "-o", "jsonpath={.spec.template.spec.containers[0].command}",
    )
    if rc != 0:
        rep.fail(f"deploy/{COLLECTOR_DEPLOY} in {namespace}", "not found — no token reports are possible")
        return info

    # The port comes from the command line, never from the ConfigMap: the Deployment overrides the
    # HTTP receiver with `--set receivers::otlp::protocols::http::endpoint=0.0.0.0:8335`, so the
    # 4318 the ConfigMap declares never listens.
    m = re.search(r"protocols::http::endpoint=0\.0\.0\.0:(\d+)", command)
    if m:
        info["http_port"] = m.group(1)
        rep.ok(f"collector HTTP receiver on :{m.group(1)}", "read from the Deployment's command")
    else:
        rep.warn(
            "collector HTTP receiver port",
            "no command-line override found; the ConfigMap's port is then authoritative "
            "(usually 4318) — confirm before pointing a workload at it",
        )

    rc, raw, _ = cluster.run(
        "-n", namespace, "get", "cm", "otel-collector-config",
        "-o", r"jsonpath={.data.base\.yaml}",
    )
    if rc != 0 or not raw:
        rep.warn("collector config readable", "cannot verify where it writes traces")
        return info

    # Parsed with regex rather than yaml so the script has no third-party dependency: only three
    # scalars are needed and they are all on their own line in every rendering we have seen.
    section = raw.split(MLFLOW_EXPORTER, 1)
    if len(section) < 2:
        rep.warn(f"exporter {MLFLOW_EXPORTER}", "not present — nothing exports to MLflow")
        return info
    body = section[1]

    def scalar(key: str) -> str | None:
        # Quotes are stripped because the collector's config renders the experiment id as a QUOTED
        # scalar ('0'), and a quote carried into the URL turns the traces query into a 400 that
        # reads like a broken MLflow.
        m = re.search(rf"{re.escape(key)}:\s*(.+)", body)
        return m.group(1).strip().strip("\"'") if m else None

    endpoint = scalar("traces_endpoint")
    exp = scalar("x-mlflow-experiment-id")
    workspace = scalar("x-mlflow-workspace")
    if endpoint:
        info["traces_endpoint"] = endpoint
        rep.ok("collector traces_endpoint", endpoint)
    else:
        rep.fail("collector traces_endpoint", "not set — spans are received and dropped")
    if exp:
        info["experiment_id"] = exp
        rep.ok("collector x-mlflow-experiment-id", exp)
    else:
        rep.warn("collector x-mlflow-experiment-id", "unset; MLflow will use the default experiment")
    if workspace:
        info["workspace"] = workspace
        rep.ok("collector x-mlflow-workspace", workspace)
    if re.search(r"^\s+auth:", body, re.M):
        info["exporter_auth"] = True
        rep.warn(
            "collector MLflow export is authenticated",
            "a stale credential here drops every span with a 401 while the run still passes; "
            "on KinD, point it at mlflow-reader (reference/kind-collector-mlflow.py)",
        )
    return info


def check_mlflow(rep: Report, cluster: Cluster, platform: str, namespace: str, collector: dict) -> None:
    rep.section("MLflow (the read half — this is what turns a run into a token report)")
    endpoint = collector.get("traces_endpoint", "")
    host = urllib.parse.urlsplit(endpoint).netloc if "://" in endpoint else ""
    svc = host.split(".")[0] if host else ""
    ns = host.split(".")[1] if host.count(".") >= 1 else namespace

    if svc:
        if cluster.exists("-n", ns, "get", "svc", svc):
            rep.ok(f"MLflow svc {svc} in {ns}", "the one the collector writes to")
        else:
            rep.fail(f"MLflow svc {svc} in {ns}", "the collector writes to a Service that does not exist")

    if platform == "kind":
        # On KinD the OIDC-gated `mlflow` refuses BOTH directions — the Service's read and the
        # collector's write — so the whole path moves to the no-auth reader.
        if cluster.exists("-n", namespace, "get", "deploy", "mlflow-reader"):
            rep.ok("deploy/mlflow-reader present")
            obj = cluster.get_json("-n", namespace, "get", "deploy", "mlflow-reader") or {}
            ready = (obj.get("status") or {}).get("readyReplicas") or 0
            if ready:
                rep.ok("mlflow-reader Ready")
            else:
                # A pod that boots cleanly and then dies leaves the evidence only in lastState.
                pods = cluster.get_json(
                    "-n", namespace, "get", "pods", "-l", "app=mlflow-reader"
                ) or {}
                reasons = []
                for pod in pods.get("items", []):
                    for st in (pod.get("status") or {}).get("containerStatuses", []) or []:
                        last = (st.get("lastState") or {}).get("terminated") or {}
                        if last:
                            reasons.append(f"{last.get('reason')} exit {last.get('exitCode')}")
                rep.fail(
                    "mlflow-reader Ready",
                    "; ".join(reasons) or "no ready replica"
                    + " (OOMKilled here means the memory limit is below one worker's ~2.3 GiB idle RSS)",
                )
            _probe_mlflow_traces(rep, cluster, namespace, collector.get("experiment_id", "0"))
        else:
            rep.fail(
                "deploy/mlflow-reader present",
                "absent — apply deploy/kind/mlflow-reader.yaml, or every token count reads 0",
            )
        if "mlflow-reader" not in endpoint:
            rep.fail(
                "collector exports to mlflow-reader",
                f"it exports to {endpoint or '(unset)'}; the OIDC-gated writer 401s the export "
                "and the run still passes — fix with reference/kind-collector-mlflow.py",
            )
        else:
            rep.ok("collector exports to mlflow-reader")
    else:
        rep.skip(
            "MLflow content probe",
            "OpenShift MLflow is SAR-gated and cross-namespace; verified by a 1-task leg instead",
        )


# The identity the SERVICE authenticates to MLflow as, and the ClusterRole RHOAI MLflow authorizes
# writes against. Both are chart values (mlflowTraceWriter.*); repeated here rather than parsed out of
# a values file because preflight reads the LIVE cluster — a rossoctl `helm upgrade` can revert a
# patched grant without touching any file in this repo.
TRACE_WRITER_SA = "mlflow-reader"
TRACE_WRITER_CLUSTERROLE = "mlflow-operator-mlflow-integration"


def mlflow_workspaces(instances: dict[str, dict] | None, teams: list[str]) -> list[str]:
    """The namespaces MLflow authorizes the Service's writes against.

    Not the workload namespaces, though on a single-cluster install they are the same string and the
    difference is invisible. MLflow SAR-checks the `x-mlflow-workspace` the Service sends, which is
    `mlflow.workspace` in the instance config — so that is what must be bound, and `teams` is only a
    fallback for when the instance config cannot be read.
    """
    found = [
        (cfg.get("mlflow") or {}).get("workspace")
        for cfg in (instances or {}).values()
        if (cfg.get("mlflow") or {}).get("workspace")
    ]
    return sorted(set(found)) or teams


def check_mlflow_write_grant(
    rep: Report, cluster: Cluster, platform: str, namespace: str, workspaces: list[str]
) -> None:
    """The write half of MLflow: can the Service's identity POST its own spans?

    Reading MLflow needs no grant, so check_mlflow above can pass while this fails. Writing is
    authorized against a ClusterRole bound in the workspace namespace, and without the binding every
    span export is 403 PERMISSION_DENIED — silently. A trace missing the Service's root
    `Agent.Session` span is dropped when the report is assembled, so the run SUCCEEDS, reports
    pass_rate 1.0, and publishes a zero-byte report.ndjson and token_report.ndjson. Nothing errors;
    the measurement is simply absent.

    Checked on the SERVICE's cluster, even in the split shape where the agents run on another one.
    The Service exports to the MLflow its instance config names, and that is a `.svc.cluster.local`
    address resolved from the Service pod — so the MLflow, and therefore the binding it reads, is
    always on this side. Looking for it on the workload cluster reports a failure on a pair that
    works.

    Matched on the GRANT, never on an object name: the first cluster to run the matrix got its binding
    by hand as `mlflow-trace-writers`, the chart renders `autobench-service-mlflow-trace-writer`, and
    both satisfy the requirement. Checking for a name would fail the working cluster.
    """
    rep.section("MLflow (the write half — without this a passing run publishes an EMPTY report)")
    if platform == "kind":
        rep.skip(
            "MLflow write grant",
            "kind runs a no-auth mlflow-reader, which authorizes nothing and needs no binding",
        )
        return

    for ns in workspaces:
        bindings = (cluster.get_json("-n", ns, "get", "rolebindings") or {}).get("items", [])
        holders = [
            rb["metadata"]["name"]
            for rb in bindings
            if (rb.get("roleRef") or {}).get("kind") == "ClusterRole"
            and (rb.get("roleRef") or {}).get("name") == TRACE_WRITER_CLUSTERROLE
            and any(
                s.get("kind") == "ServiceAccount" and s.get("name") == TRACE_WRITER_SA
                for s in rb.get("subjects") or []
            )
        ]
        if holders:
            rep.ok(
                f"{ns}: {TRACE_WRITER_SA} may write traces",
                f"via RoleBinding/{holders[0]}" + (f" (+{len(holders) - 1} more)" if len(holders) > 1 else ""),
            )
        else:
            rep.fail(
                f"{ns}: {TRACE_WRITER_SA} may write traces",
                f"no RoleBinding in {ns} grants ClusterRole/{TRACE_WRITER_CLUSTERROLE} to "
                f"ServiceAccount {namespace}/{TRACE_WRITER_SA} — every span export 403s and the run "
                "still passes with an empty token report. Install with "
                "mlflowTraceWriter.enabled=true, whose namespaces must list this MLflow workspace "
                "(deploy/helm/values-openshift.yaml sets it)",
            )


def _probe_mlflow_traces(rep: Report, cluster: Cluster, namespace: str, experiment_id: str) -> None:
    """Query the traces API from inside the MLflow pod.

    From outside, the API server's service proxy sends its own Host header and MLflow 3.x rejects it
    as a DNS-rebinding attempt (a 403 that looks like an authz problem). `localhost:5000` is in the
    reader's allow-list, so an exec is both the simplest and the only reliable probe. Note the
    `experiment_ids` parameter is required — without it the API answers 400, which reads like a
    broken server rather than a malformed query.
    """
    script = (
        "import json,urllib.request;"
        f"u='http://localhost:5000/api/2.0/mlflow/traces?experiment_ids={experiment_id}&max_results=1';"
        "r=urllib.request.urlopen(u,timeout=15);d=json.load(r);"
        "print(r.status,len(d.get('traces',[])))"
    )
    rc, out, err = cluster.run(
        "-n", namespace, "exec", "deploy/mlflow-reader", "--", "python", "-c", script, timeout=60
    )
    if rc != 0:
        rep.fail("MLflow traces API answers", (err or out).splitlines()[-1] if (err or out) else f"exit {rc}")
        return
    parts = out.split()
    status, count = (parts + ["?", "?"])[:2]
    if status == "200":
        detail = f"experiment {experiment_id}, {count} trace(s) visible"
        if count == "0":
            rep.warn("MLflow traces API answers 200", detail + " — nothing has been written yet")
        else:
            rep.ok("MLflow traces API answers 200", detail)
    else:
        rep.fail("MLflow traces API answers 200", f"got {status}")


def check_ingress(rep: Report, cluster: Cluster, platform: str, namespace: str, gateway: str) -> None:
    rep.section("Ingress for the Service")
    if platform == "openshift":
        if cluster.api_resource("route.openshift.io", "routes"):
            rep.ok("route.openshift.io/v1 served", "the chart renders a Route with edge TLS")
        else:
            rep.fail("route.openshift.io/v1 served", "not an OpenShift cluster? use --platform kind")
    else:
        if cluster.api_resource("gateway.networking.k8s.io", "httproutes"):
            rep.ok("gateway.networking.k8s.io/v1 HTTPRoute served")
        else:
            rep.fail("HTTPRoute served", "Gateway API not installed")
        obj = cluster.get_json("-n", namespace, "get", "gateway", gateway)
        if obj is None:
            rep.fail(
                f"gateway {gateway} in {namespace}",
                "absent — the chart's HTTPRoute would have no parent and never resolve",
            )
        else:
            programmed = [
                c for c in (obj.get("status") or {}).get("conditions", [])
                if c.get("type") == "Programmed" and c.get("status") == "True"
            ]
            (rep.ok if programmed else rep.fail)(
                f"gateway {gateway} Programmed", "" if programmed else "the gateway is not ready"
            )


def read_instances(cluster: Cluster, namespace: str) -> dict[str, dict] | None:
    """Parse the instance Secret in memory. Returns None when the Secret is absent."""
    data = cluster.secret_data(namespace, INSTANCES_SECRET)
    if data is None:
        return None
    out: dict[str, dict] = {}
    for name, raw in data.items():
        try:
            out[name] = json.loads(raw)
        except json.JSONDecodeError:
            out[name] = {}
    return out


def check_instance_config(
    rep: Report, cluster: Cluster, namespace: str, platform: str, collector: dict,
    instances: dict[str, dict] | None, llm_profile: str | None = None,
) -> None:
    rep.section(f"Instance config (Secret {INSTANCES_SECRET})")
    if instances is None:
        rep.warn(
            f"Secret {INSTANCES_SECRET}",
            "absent — the chart references but never creates it; generate it with "
            "reference/{kind,ocp}-service-bootstrap.sh before installing",
        )
        return
    rep.ok(f"Secret {INSTANCES_SECRET}",
           f"{len(instances)} instance file(s): {', '.join(sorted(instances))}")

    for name, cfg in sorted(instances.items()):
        if not cfg:
            rep.fail(f"{name} parses as JSON", "not valid JSON — the Service will skip this instance")
            continue
        iss = cfg.get("iss", "(no iss)")
        rep.ok(f"{name}: iss", iss)

        cred = cfg.get("service_credential") or {}
        missing = [k for k in ("client_id", "username", "password") if not cred.get(k)]
        if missing:
            rep.fail(f"{name}: service_credential", f"missing {', '.join(missing)}")
        else:
            rep.ok(
                f"{name}: service_credential",
                f"user {cred['username']}, password sha8 {sha8(cred['password'])}",
            )

        # MLflow: the Service reads here, and it must be the same place and experiment the
        # collector writes to. A disagreement produces a passing run with zero tokens.
        mlf = cfg.get("mlflow") or {}
        tracking = mlf.get("tracking_url") or ""
        if not tracking:
            rep.warn(f"{name}: mlflow.tracking_url", "unset — the run will publish no token report")
        else:
            rep.ok(f"{name}: mlflow.tracking_url", tracking)
            writer = collector.get("traces_endpoint", "")
            read_host = urllib.parse.urlsplit(tracking).netloc
            if writer and read_host and read_host.split(":")[0] not in writer:
                rep.fail(
                    f"{name}: reads the MLflow the collector writes to",
                    f"Service reads {read_host}, collector writes {writer}",
                )
            elif writer:
                rep.ok(f"{name}: reads the MLflow the collector writes to")
        if not (mlf.get("bearer_token") or (mlf.get("client_id") and mlf.get("client_secret"))
                or (mlf.get("username") and mlf.get("password"))):
            rep.fail(
                f"{name}: mlflow auth",
                "no bearer_token, client-credentials or username/password — the Service cannot mint "
                "a token, the read fails soft, and the report is empty. A no-auth reader still needs "
                "a placeholder bearer_token",
            )
        else:
            rep.ok(f"{name}: mlflow auth configured")
        want = collector.get("experiment_id")
        got = str(mlf.get("experiment_id", ""))
        if want and got and want != got:
            rep.fail(
                f"{name}: mlflow.experiment_id matches the collector",
                f"config says {got}, the collector writes to {want}",
            )
        elif want:
            rep.ok(f"{name}: mlflow.experiment_id matches the collector", got)

        # Workload OTEL: the agent must reach a port the collector actually binds — directly
        # in-cluster, or through an edge Route when the workloads live on another cluster.
        otel = cfg.get("workload_otel") or {}
        if not otel.get("enabled"):
            rep.warn(
                f"{name}: workload_otel disabled",
                "the agent exports no spans, so there is no per-task token data",
            )
        else:
            _check_otel_endpoint(rep, cluster, namespace, name, otel.get("endpoint") or "", collector)

        # The runner: `direct` emits no agent-side spans at all, which looks exactly like a broken
        # collector — a passing run, model "unknown", every token count 0.
        runner = cfg.get("workload_agent_runner")
        if runner == "direct":
            rep.warn(
                f"{name}: workload_agent_runner=direct",
                "no agent spans are emitted; use `service` if you want token reports",
            )
        elif runner:
            rep.ok(f"{name}: workload_agent_runner", runner)

        llm = cfg.get("workload_llm") or {}
        base = llm.get("api_base") or ""
        if base:
            rep.ok(f"{name}: workload_llm", f"{urllib.parse.urlsplit(base).netloc} "
                                            f"model {llm.get('default_model') or '(benchmark default)'}")
            # The two gateways keep separate key tables, so a base that disagrees with the key in
            # openai-secret does not fail closed — it 401s per completion, mid-run, and the leg
            # finishes with zeroes. What the base must agree with is the DECLARED profile, not the
            # platform: an OpenShift cluster on the organisation's intranet uses the internal
            # gateway, exactly like a local kind cluster, so deriving it from `platform` (as this
            # check used to) fails a correct install.
            expect = llm_profile_base(llm_profile) if llm_profile else None
            if llm_profile and expect:
                if base == expect:
                    rep.ok(f"{name}: LLM gateway matches the {llm_profile} profile")
                else:
                    rep.fail(
                        f"{name}: LLM gateway matches the {llm_profile} profile",
                        f"instance points at {urllib.parse.urlsplit(base).netloc}, the "
                        f"{llm_profile} profile declares "
                        f"{urllib.parse.urlsplit(expect).netloc} — separate key tables, so this "
                        "401s per completion mid-run rather than failing at deploy",
                    )
            elif llm_profile:
                rep.warn(
                    f"{name}: LLM gateway vs the {llm_profile} profile",
                    f"{llm_profile.upper()}_LLM_API_BASE is unset here and absent from "
                    f"{LLM_PROFILE_ENV_FILE}, so the base cannot be verified",
                )
            else:
                rep.warn(
                    f"{name}: LLM gateway profile",
                    "undeclared — pass --llm-profile, or --values with llmProfile set, to check "
                    "the base against the gateway whose key table issued the key",
                )
        else:
            rep.warn(f"{name}: workload_llm.api_base", "unset — the agent uses the image default")

        s3 = cfg.get("s3") or {}
        if s3.get("bucket") and s3.get("access_key_id"):
            rep.ok(
                f"{name}: s3",
                f"bucket {s3['bucket']} prefix {s3.get('prefix') or '(none)'} "
                f"key sha8 {sha8(s3['access_key_id'])}",
            )
        else:
            rep.warn(f"{name}: s3", "no bucket/credentials — the run scores but publishes no artifacts")


def _check_otel_endpoint(
    rep: Report, cluster: Cluster, namespace: str, name: str, endpoint: str, collector: dict
) -> None:
    """Does the agent's OTLP endpoint land on the port the collector binds?

    Two shapes, and conflating them produces a false alarm. Same-cluster workloads dial the
    collector's Service directly, so the endpoint's port must equal the one the Deployment's command
    binds. Cross-cluster workloads (ykt3's Service drives ykt2's agents) dial an **edge Route** on
    :443, and 443 is then correct — what has to be verified is that the Route targets the collector
    and its :8335 port, not the endpoint's own port number.
    """
    parts = urllib.parse.urlsplit(endpoint)
    host, port = parts.hostname or "", parts.port
    want_port = collector.get("http_port")
    if host.endswith(".svc.cluster.local") or "." not in host:
        if want_port and str(port) != want_port:
            rep.fail(
                f"{name}: workload_otel port",
                f"points at :{port}; the collector binds :{want_port}. A wrong port hard-fails the "
                "agent into CrashLoopBackOff, surfacing as a 424 on the run",
            )
        else:
            rep.ok(f"{name}: workload_otel endpoint", endpoint)
        return

    routes = cluster.get_json("-n", namespace, "get", "routes") or {}
    matches = [r for r in routes.get("items", []) if (r.get("spec") or {}).get("host") == host]
    if not matches:
        rep.warn(
            f"{name}: workload_otel endpoint", f"{endpoint} is external, and no Route in {namespace} "
            "serves that host — verify it by hand (an unreachable collector crash-loops the agent)",
        )
        return
    spec = matches[0]["spec"]
    target = str((spec.get("port") or {}).get("targetPort", ""))
    to = (spec.get("to") or {}).get("name", "")
    detail = f"{endpoint} -> Route {matches[0]['metadata']['name']} -> {to}:{target or '(default)'}"
    if to != COLLECTOR_DEPLOY:
        rep.fail(f"{name}: workload_otel Route target", detail + f", not {COLLECTOR_DEPLOY}")
        return
    # The Route's targetPort may be a port NAME, so resolve it through the Service.
    svc = cluster.get_json("-n", namespace, "get", "svc", to) or {}
    ports = {str(p.get("name", "")): str(p.get("port")) for p in (svc.get("spec") or {}).get("ports", [])}
    resolved = ports.get(target, target)
    if want_port and resolved and resolved != want_port:
        rep.fail(
            f"{name}: workload_otel Route port",
            detail + f" resolves to :{resolved}; the collector binds :{want_port}",
        )
    else:
        rep.ok(f"{name}: workload_otel endpoint (cross-cluster, via Route)", detail)


def _post_form(url: str, form: dict[str, str], timeout: int = 30) -> tuple[int, dict | str]:
    """POST a form and return (status, parsed body). Never raises on an HTTP error status.

    Keycloak puts the *reason* a password grant failed in the 400 body, and that reason is the
    whole value of this check — "wrong password" and "the realm wants firstName/lastName" are
    different jobs for whoever is installing. urlopen raises on 4xx, so the body has to be read
    off the exception.
    """
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(form).encode(), method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return 0, str(exc)


def _get_json_authed(url: str, token: str, timeout: int = 30) -> tuple[int, object]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return 0, str(exc)


def _ropc_cause(status: int, body: dict | str, user_state: str | None = None) -> str:
    """Turn Keycloak's refusal into the thing the installer has to go and fix.

    `user_state` is what the Admin API tier found, when it ran — "ok" (a password credential is
    set), "no-password", or "absent". It resolves the ambiguity Keycloak leaves in `invalid_grant`,
    which is the same response for a wrong password, a user with no password, and no user at all.
    """
    if status == 0:
        return f"could not reach the token endpoint: {body}"
    err = desc = ""
    if isinstance(body, dict):
        err = str(body.get("error") or "")
        desc = str(body.get("error_description") or "")
    blob = f"{err} {desc}".lower()
    if "not fully set up" in blob:
        return (
            "'Account is not fully set up' — the PASSWORD may be correct; the user has a pending "
            "required action, or no firstName/lastName, which the rossoctl realm's user profile "
            "requires before it will issue a ROPC token"
        )
    if "disabled" in blob:
        return "the account is disabled in Keycloak"
    if "invalid user credentials" in blob or err == "invalid_grant":
        if user_state == "ok":
            return (
                "'Invalid user credentials' — a password credential IS set on this user (checked "
                "above), so the password given to this script is not the one Keycloak holds. Reset "
                "it with reference/keycloak-ensure-user.sh, or supply the right one"
            )
        if user_state == "no-password":
            return "'Invalid user credentials' — and the user has NO password credential (see above)"
        if user_state == "absent":
            return "'Invalid user credentials' — and no such user exists in the realm (see above)"
        return (
            "'Invalid user credentials' — the password does not match, or the user has no password "
            "credential set at all. Keycloak reports these identically; supply a Keycloak admin "
            "credential (KC_ADMIN_PASSWORD, or an in-cluster keycloak-initial-admin Secret) and "
            "this script will tell you which"
        )
    if err in ("unauthorized_client", "invalid_client"):
        return (
            f"{err} — the client is confidential or has directAccessGrantsEnabled=false; set "
            "KC_SERVICE_CLIENT_SECRET, or enable Direct Access Grants "
            "(reference/keycloak-ensure-user.sh does it idempotently)"
        )
    detail = desc or err or str(body)
    return f"HTTP {status}: {detail}"


def read_password_file(path: str) -> str:
    """Read a credential file, refusing one other users can read.

    Same rule as every other credential file in reference/: `600` or `400`. The trailing newline a
    text editor adds is stripped — a password with a newline welded on fails ROPC with exactly the
    message a wrong password gives, and that has cost an afternoon before.
    """
    mode = os.stat(path).st_mode & 0o777
    if mode not in (0o600, 0o400):
        raise PermissionError(f"{path} must be chmod 600 (is {mode:o})")
    with open(path, encoding="utf-8") as fh:
        return fh.readline().rstrip("\n")


def resolve_service_credential(
    args: argparse.Namespace, instances: dict[str, dict] | None
) -> tuple[str | None, str | None, str, str | None]:
    """Where the `benchmarker` credential comes from: (username, password, source, error).

    Precedence is most-explicit-first, ending at the instance Secret — which is the interesting
    one, because that is the copy the Service will actually present to Rossoctl. Checking a
    password from your shell proves something about your shell; checking the one in the Secret
    proves something about the install.
    """
    user = args.username or os.environ.get("KC_SERVICE_USERNAME")
    if args.password_file:
        try:
            return user, read_password_file(args.password_file), f"file {args.password_file}", None
        except OSError as exc:
            return user, None, f"file {args.password_file}", str(exc)
    if args.password_stdin:
        pw = sys.stdin.readline().rstrip("\n")
        return user, pw or None, "stdin", None if pw else "nothing on stdin"
    if args.password:
        return user, args.password, "--password (argv)", None
    if os.environ.get("KC_SERVICE_PASSWORD"):
        return user, os.environ["KC_SERVICE_PASSWORD"], "env KC_SERVICE_PASSWORD", None
    for name, cfg in sorted((instances or {}).items()):
        cred = cfg.get("service_credential") or {}
        if cred.get("password"):
            return (
                user or cred.get("username"),
                cred["password"],
                f"Secret {INSTANCES_SECRET} ({name})",
                None,
            )
    return user, None, "(none)", None


def _keycloak_admin_token(rep: Report, cluster: Cluster, base: str) -> str | None:
    """A master-realm admin token, if one can be had without asking anybody to type anything.

    Optional by design: it buys the one distinction ROPC cannot make — "no password is set" versus
    "the password is wrong". On KinD the admin password is sitting in `keycloak-initial-admin`; on a
    managed OpenShift Keycloak it usually is not, and then this tier just does not run.
    """
    password = os.environ.get("KC_ADMIN_PASSWORD")
    username = os.environ.get("KC_ADMIN_USER", "admin")
    if not password:
        data = cluster.secret_data("keycloak", "keycloak-initial-admin") or {}
        if data.get("password"):
            password = data["password"].decode("utf-8", "replace")
            username = (data.get("username") or b"admin").decode("utf-8", "replace")
    if not password:
        return None
    status, body = _post_form(
        f"{base}/realms/master/protocol/openid-connect/token",
        {"client_id": "admin-cli", "grant_type": "password",
         "username": username, "password": password},
    )
    if status == 200 and isinstance(body, dict):
        return body.get("access_token")
    rep.warn(
        "Keycloak admin API reachable",
        f"admin login as {username} failed ({_ropc_cause(status, body)}) — the "
        "'is a password actually set?' check is skipped",
    )
    return None


def _check_user_via_admin_api(
    rep: Report, cluster: Cluster, base: str, realm: str, username: str
) -> str | None:
    """Answer the literal question — is a password SET on this user — from the Admin REST API.

    Returns "ok", "no-password" or "absent" when it could tell; None when the tier did not run.
    """
    token = _keycloak_admin_token(rep, cluster, base)
    if not token:
        rep.skip(
            f"password credential set on {username}",
            "no Keycloak admin credential available (export KC_ADMIN_PASSWORD to enable); the "
            "ROPC login below is then the only check of the password",
        )
        return None
    admin = f"{base}/admin/realms/{urllib.parse.quote(realm)}"
    status, body = _get_json_authed(
        f"{admin}/users?username={urllib.parse.quote(username)}&exact=true", token
    )
    if status != 200 or not isinstance(body, list):
        rep.warn(f"user {username} exists in realm {realm}", f"user lookup failed: HTTP {status}")
        return None
    if not body:
        rep.fail(
            f"user {username} exists in realm {realm}",
            "absent — create it with reference/keycloak-ensure-user.sh",
        )
        return "absent"
    user_obj = body[0]
    uid = user_obj.get("id", "")
    rep.ok(f"user {username} exists in realm {realm}", f"id {uid}")
    if not user_obj.get("enabled", True):
        rep.fail(f"user {username} enabled", "disabled in Keycloak — every ROPC login will fail")
    # The realm's user profile requires both, and without them ROPC 400s "Account is not fully set
    # up" even though the password is perfectly correct. keycloak-ensure-user.sh does not set them.
    missing = [f for f in ("firstName", "lastName") if not user_obj.get(f)]
    if missing:
        rep.fail(
            f"user {username} profile complete",
            f"{', '.join(missing)} unset — the rossoctl realm requires both, and ROPC then 400s "
            "'Account is not fully set up' with a correct password",
        )
    if user_obj.get("requiredActions"):
        rep.fail(
            f"user {username} has no pending required actions",
            f"{', '.join(user_obj['requiredActions'])} — a required action blocks the password grant",
        )

    status, creds = _get_json_authed(f"{admin}/users/{uid}/credentials", token)
    if status != 200 or not isinstance(creds, list):
        rep.warn(f"password credential set on {username}", f"credential lookup failed: HTTP {status}")
        return None
    types = [c.get("type") for c in creds if isinstance(c, dict)]
    if "password" in types:
        # Only the type — a credential representation never carries the secret itself, and nothing
        # here would print it if it did.
        rep.ok(f"password credential set on {username}", f"credential type(s): {', '.join(types)}")
        return "ok"
    rep.fail(
        f"password credential set on {username}",
        "the user has NO password credential — set one with reference/keycloak-ensure-user.sh "
        f"(types present: {', '.join(types) or 'none'})",
    )
    return "no-password"


def check_identity(
    rep: Report, cluster: Cluster, instances: dict[str, dict] | None,
    args: argparse.Namespace, iss_hint: str | None,
) -> None:
    """The `benchmarker` credential: is a password set, does it work, is it the one the Service has.

    Not optional, and not the same check three times. Every `/deploy` the Service makes is a ROPC
    login as this user, so a credential that is absent, stale or blocked by a required action does
    not degrade the install — it makes every benchmark run fail with a 502 wrapping a 403.
    """
    rep.section("Identity — the benchmarker credential every /deploy authenticates with")
    user, password, source, err = resolve_service_credential(args, instances)
    client_id = os.environ.get("KC_SERVICE_CLIENT_ID", "rossoctl")
    client_secret = os.environ.get("KC_SERVICE_CLIENT_SECRET")
    user = user or "benchmarker"

    if err:
        rep.fail(f"benchmarker password from {source}", err)
        return
    if not password:
        rep.fail(
            "benchmarker password available",
            "not supplied and not in the instance Secret. Give it to this script one of four ways: "
            "--password-file <chmod-600 file> (preferred), --password-stdin, --password <value> "
            "(visible in `ps`), or export KC_SERVICE_PASSWORD",
        )
        return
    rep.ok(f"benchmarker password from {source}", f"user {user}, sha8 {sha8(password)}")
    if source == "--password (argv)":
        rep.warn(
            "password passed on the command line",
            "argv is world-readable (`ps`, /proc/<pid>/cmdline) and lands in your shell history — "
            "prefer --password-file or --password-stdin",
        )

    # The copy in the Secret is the one the Service presents. Validating a different one is how a
    # green preflight coexists with a Service that 502s on every deploy.
    for name, cfg in sorted((instances or {}).items()):
        cred = cfg.get("service_credential") or {}
        if not cred.get("password") or source.startswith(f"Secret {INSTANCES_SECRET}"):
            continue
        if cred.get("username") and cred["username"] != user:
            # Checking a different user on purpose (--username); the two passwords have no reason
            # to agree, and reporting that as a failure is noise.
            continue
        if cred["password"] == password:
            rep.ok(f"{name}: instance password matches the one checked here")
        else:
            rep.fail(
                f"{name}: instance password matches the one checked here",
                f"the Secret holds sha8 {sha8(cred['password'])}, this check used "
                f"sha8 {sha8(password)} — the Service will present the Secret's, so a pass below "
                "proves nothing about it. Regenerate the instance file, or drop the override",
            )

    iss = args.iss or os.environ.get("KC_ISS") or iss_hint or _discover_iss(cluster, args)
    if not iss:
        rep.fail(
            "issuer known",
            "cannot check the credential without one — pass --iss "
            "https://<keycloak>/realms/<realm>, or create the instance Secret first",
        )
        return
    iss = iss.rstrip("/")
    base, _, realm = iss.partition("/realms/")

    # Existence and "is a password set" first, so that when the login below fails its explanation is
    # already on screen rather than being a list of things it might have been.
    user_state = _check_user_via_admin_api(rep, cluster, base, realm, user) if realm else None

    form = {"client_id": client_id, "grant_type": "password",
            "username": user, "password": password}
    if client_secret:
        form["client_secret"] = client_secret
    status, body = _post_form(f"{iss}/protocol/openid-connect/token", form)
    token = body.get("access_token", "") if (status == 200 and isinstance(body, dict)) else ""
    if token:
        rep.ok(f"ROPC login as {user}", f"against {iss}")
    else:
        rep.fail(f"ROPC login as {user}", _ropc_cause(status, body, user_state))

    if token:
        # The realm role every /deploy needs. Without it the Service surfaces the operator's 403 as
        # a 502, which reads like the Service is broken rather than unauthorised.
        try:
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            roles = (
                json.loads(base64.urlsafe_b64decode(payload))
                .get("realm_access", {}).get("roles", [])
            )
        except (IndexError, ValueError, json.JSONDecodeError):
            rep.warn("realm role rossoctl-operator", "could not decode the token payload")
            roles = None
        if roles is not None:
            if "rossoctl-operator" in roles:
                rep.ok("realm role rossoctl-operator")
            else:
                rep.fail(
                    "realm role rossoctl-operator",
                    "absent from the token — /deploy will fail with a 502 wrapping a 403",
                )


def _discover_iss(cluster: Cluster, args: argparse.Namespace) -> str | None:
    """Find Keycloak's issuer from the cluster, for a preflight run before the Secret exists.

    A Route on OpenShift, an HTTPRoute on KinD. Deliberately does not guess a hostname: an issuer
    that is merely plausible produces a confident-looking FAIL against a Keycloak nobody uses.
    """
    realm = args.realm
    obj = cluster.get_json("-n", "keycloak", "get", "route", "keycloak")
    host = ((obj or {}).get("spec") or {}).get("host")
    if host:
        return f"https://{host}/realms/{realm}"
    routes = cluster.get_json("-n", "keycloak", "get", "httproute", "keycloak") or {}
    hostnames = ((routes.get("spec") or {}).get("hostnames") or [])
    if hostnames:
        # KinD publishes the istio gateway on a host port, and the issuer carries it.
        port = f":{args.kind_gateway_port}" if args.kind_gateway_port else ""
        return f"http://{hostnames[0]}{port}/realms/{realm}"
    return None


def check_service_install(rep: Report, cluster: Cluster, namespace: str, image: str | None) -> None:
    rep.section("Existing AutoBench install (post-install audit)")
    obj = cluster.get_json("-n", namespace, "get", "deploy", SERVICE_DEPLOY)
    if obj is None:
        rep.skip(f"deploy/{SERVICE_DEPLOY}", "not installed yet — nothing to audit")
        return
    spec_image = (obj["spec"]["template"]["spec"]["containers"][0]).get("image", "")
    ready = (obj.get("status") or {}).get("readyReplicas") or 0
    (rep.ok if ready else rep.fail)(
        f"deploy/{SERVICE_DEPLOY} Ready", "" if ready else "no ready replica"
    )
    rep.ok("Service image (spec)", spec_image)
    if image and image != spec_image:
        rep.warn("Service image matches --image", f"deployed {spec_image}, expected {image}")

    # A tag tells you nothing about what is running; the digest does.
    pods = cluster.get_json("-n", namespace, "get", "pods", "-l", f"app={SERVICE_DEPLOY}") or {}
    for pod in pods.get("items", []):
        for st in (pod.get("status") or {}).get("containerStatuses", []) or []:
            if st.get("imageID"):
                rep.ok("Service image (running digest)", st["imageID"])

    # Helm ownership, read from the release Secret rather than `helm list`, which has returned
    # identical output for two different contexts.
    rel = cluster.get_json(
        "-n", namespace, "get", "secret", "-l", "owner=helm,name=autobench"
    ) or {}
    items = rel.get("items", [])
    if items:
        revisions = sorted(i["metadata"]["labels"].get("version", "?") for i in items)
        rep.ok("Helm release `autobench` owns this install", f"revision(s) {', '.join(revisions)}")
    else:
        rep.warn(
            "Helm release `autobench`",
            "no release Secret — this install came from the raw manifests. `helm upgrade --install` "
            "needs the adoption labels first (see the Admin Guide)",
        )


def check_chart(rep: Report, chart_dir: str, platform: str) -> None:
    rep.section("Chart (local working tree)")
    if not os.path.isdir(chart_dir):
        rep.skip("helm lint", f"{chart_dir} not found — run from the repo root to check the chart")
        return
    p = subprocess.run(["helm", "lint", chart_dir, "--set", f"platform={platform}"],
                       capture_output=True, text=True)
    if p.returncode == 0:
        rep.ok(f"helm lint {chart_dir} (platform={platform})")
    else:
        rep.fail("helm lint", (p.stdout + p.stderr).strip().splitlines()[-1])
    parity = os.path.join("reference", "helm-parity-check.py")
    if os.path.exists(parity):
        p = subprocess.run([sys.executable, parity], capture_output=True, text=True)
        if p.returncode == 0:
            rep.ok("chart/manifest parity", "the chart renders what deploy/*.yaml renders")
        else:
            rep.fail("chart/manifest parity", (p.stdout + p.stderr).strip().splitlines()[-1])


# --- entry point -----------------------------------------------------------


def infer_platform(context: str | None) -> str:
    return "kind" if (context or "").startswith("kind-") else "openshift"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--context", help="kubectl context for the SERVICE cluster (default: current)")
    ap.add_argument("--workload-context",
                    help="kubectl context for the cluster the agents run on, when it is a different "
                         "one (ykt3 drives ykt2). Default: the same cluster, unless the instance "
                         "config carries endpoint templates, in which case the workload checks are "
                         "skipped rather than run against the wrong cluster")
    ap.add_argument("--platform", choices=("kind", "openshift"),
                    help="default: kind when the context starts with kind-, else openshift")
    ap.add_argument("--namespace", default=DEFAULT_NS, help="Service namespace (default: %(default)s)")
    ap.add_argument("--teams", default=",".join(DEFAULT_TEAMS),
                    help="comma-separated workload namespaces to check — the ones you deploy into "
                         "(default: %(default)s; every run12 spec names team1)")
    ap.add_argument("--llm-profile", choices=("intranet", "internet"),
                    help="which LLM gateway this cluster must use (default: $LLM_PROFILE, or the "
                         "llmProfile in --values). Orthogonal to --platform: an OpenShift cluster on "
                         "the intranet uses `intranet`")
    ap.add_argument("--values", help="chart values file to read llmProfile from, e.g. "
                                     "deploy/helm/values-kind.yaml")
    ap.add_argument("--username", help="Keycloak user the Service logs in as (default: "
                                       "$KC_SERVICE_USERNAME, the instance Secret's, else benchmarker)")
    pw = ap.add_mutually_exclusive_group()
    pw.add_argument("--password-file", help="read the benchmarker password from a chmod-600 file "
                                           "(preferred: the value never reaches argv)")
    pw.add_argument("--password-stdin", action="store_true",
                    help="read the benchmarker password from stdin")
    pw.add_argument("--password", help="the benchmarker password literally. WARNING: argv is "
                                      "world-readable via `ps` — prefer --password-file")
    ap.add_argument("--iss", help="Keycloak issuer, e.g. https://<host>/realms/rossoctl (default: "
                                 "$KC_ISS, the instance Secret's iss, else discovered from the "
                                 "Keycloak Route/HTTPRoute)")
    ap.add_argument("--realm", default="rossoctl",
                    help="realm to use when the issuer has to be discovered (default: %(default)s)")
    ap.add_argument("--kind-gateway-port", default="8080",
                    help="kind only: host port the istio gateway is published on, used when the "
                         "issuer is discovered from an HTTPRoute (default: %(default)s)")
    ap.add_argument("--gateway", default="http", help="kind only: Gateway name (default: %(default)s)")
    ap.add_argument("--plugin-legs", action="store_true",
                    help="the plugin legs (#5-#8) are in scope, so a missing or half-configured "
                         "IBAC judge is a FAILURE rather than a warning. Without a judge those legs "
                         "still pass — with the ibac plugin inert and nothing measured")
    ap.add_argument("--image", help="expected Service image, to compare against what is deployed")
    ap.add_argument("--chart", default=CHART_DIR, help="chart directory (default: %(default)s)")
    ap.add_argument("--skip-chart", action="store_true", help="skip the local lint/parity checks")
    ap.add_argument("--json", action="store_true", help="emit results as JSON on stdout")
    args = ap.parse_args()

    context = args.context
    if not context:
        p = subprocess.run(["kubectl", "config", "current-context"], capture_output=True, text=True)
        context = p.stdout.strip() or None
    platform = args.platform or infer_platform(context)
    teams = [t for t in args.teams.split(",") if t]
    # Precedence: the flag, then a values file (one declaration shared with the release), then the
    # environment. Never inferred from the platform — that is the mistake this replaced.
    llm_profile = args.llm_profile or (llm_profile_from_values(args.values) if args.values else None) \
        or os.environ.get("LLM_PROFILE") or None

    rep = Report(quiet=args.json)
    if not args.json:
        print(f"AutoBench preflight — platform={platform} context={context or '(current)'} "
              f"namespace={args.namespace} llm-profile={llm_profile or '(undeclared)'}")

    cluster = Cluster(context)
    check_tooling(rep, platform, cluster)
    if check_cluster(rep, cluster):
        # The instance config is read first because it is what says whether this is a single-cluster
        # install or the split shape, and the workload checks belong on the workload cluster.
        instances = read_instances(cluster, args.namespace)
        cross_cluster = any(
            cfg.get("agent_endpoint_template") or cfg.get("mcp_endpoint_template")
            for cfg in (instances or {}).values()
        )
        if args.workload_context:
            workload: Cluster | None = Cluster(args.workload_context)
        else:
            workload = None if cross_cluster else cluster
        if cross_cluster and not args.workload_context and not args.json:
            print("  (this instance drives workloads on another cluster — "
                  "pass --workload-context to check that side too)")

        check_rossoctl(rep, cluster, args.namespace)
        check_namespaces(rep, cluster, args.namespace, teams, workload)
        check_workload_secrets(rep, workload, teams)
        check_ibac_judge(rep, cluster, args.namespace, required=args.plugin_legs)
        collector = check_collector(rep, cluster, args.namespace)
        check_mlflow(rep, cluster, platform, args.namespace, collector)
        check_mlflow_write_grant(rep, cluster, platform, args.namespace,
                                 mlflow_workspaces(instances, teams))
        check_ingress(rep, cluster, platform, args.namespace, args.gateway)
        check_instance_config(rep, cluster, args.namespace, platform, collector, instances,
                              llm_profile=llm_profile)
        iss_hint = next((cfg.get("iss") for cfg in (instances or {}).values() if cfg.get("iss")), None)
        check_identity(rep, cluster, instances, args, iss_hint)
        check_service_install(rep, cluster, args.namespace, args.image)
    if not args.skip_chart:
        check_chart(rep, args.chart, platform)

    failures, warnings = rep.count(FAIL), rep.count(WARN)
    if args.json:
        print(json.dumps({"platform": platform, "context": context,
                          "failures": failures, "warnings": warnings, "checks": rep.rows}, indent=2))
    else:
        print()
        verdict = "ready to install" if not failures else "NOT ready — fix the failures above"
        print(f"{rep.count(OK)} ok, {warnings} warning(s), {failures} failure(s) — {verdict}")
        if not failures:
            print("A clean preflight is necessary, not sufficient: only a 1-task leg with a non-zero "
                  "token row proves the whole chain.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
