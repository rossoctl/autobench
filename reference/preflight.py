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

Optional, and skipped with a note when absent: `KC_SERVICE_USERNAME` + `KC_SERVICE_PASSWORD` in
the environment enable the identity checks (ROPC login, and the `rossoctl-operator` realm role
without which every `/deploy` surfaces a 403 as a 502).
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
DEFAULT_TEAMS = ("team1", "team2")
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
        # own team1/team2 hold nothing and checking them there would fail on all three counts.
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

    # Cross-namespace consistency: team1 and team2 must hold the SAME gateway key, or a leg that
    # lands in team2 fails while the identical leg in team1 passes.
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
    instances: dict[str, dict] | None
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
            # The two gateways keep separate key tables, so a key moved between platforms does not
            # fail closed — it 401s per completion, mid-run.
            internal = "vpc-int" in base
            if platform == "openshift" and internal:
                rep.fail(f"{name}: LLM gateway", "names the INTERNAL gateway, which is KinD-only")
            elif platform == "kind" and not internal:
                rep.warn(
                    f"{name}: LLM gateway",
                    "KinD normally needs the INTERNAL gateway; the external one is unroutable "
                    "from here on a split-tunnel VPN",
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


def check_identity(rep: Report, iss_hint: str | None) -> None:
    rep.section("Identity (optional — needs KC_SERVICE_USERNAME + KC_SERVICE_PASSWORD)")
    user = os.environ.get("KC_SERVICE_USERNAME")
    password = os.environ.get("KC_SERVICE_PASSWORD")
    client_id = os.environ.get("KC_SERVICE_CLIENT_ID", "rossoctl")
    client_secret = os.environ.get("KC_SERVICE_CLIENT_SECRET")
    iss = os.environ.get("KC_ISS") or iss_hint
    if not (user and password):
        rep.skip("ROPC login", "KC_SERVICE_USERNAME / KC_SERVICE_PASSWORD not set")
        return
    if not iss:
        rep.skip("ROPC login", "no issuer known (set KC_ISS, or create the instance Secret first)")
        return

    form = {
        "client_id": client_id,
        "grant_type": "password",
        "username": user,
        "password": password,
    }
    if client_secret:
        form["client_secret"] = client_secret
    req = urllib.request.Request(
        f"{iss.rstrip('/')}/protocol/openid-connect/token",
        data=urllib.parse.urlencode(form).encode(),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            token = json.load(resp).get("access_token", "")
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
        rep.fail(
            f"ROPC login as {user}",
            f"{exc} — a 400 'Account is not fully set up' means the realm requires "
            "firstName/lastName on the user",
        )
        return
    rep.ok(f"ROPC login as {user}")

    # The realm role every /deploy needs. Without it the Service surfaces the operator's 403 as a 502.
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        roles = json.loads(base64.urlsafe_b64decode(payload)).get("realm_access", {}).get("roles", [])
    except (IndexError, ValueError, json.JSONDecodeError):
        rep.warn("realm role rossoctl-operator", "could not decode the token payload")
        return
    if "rossoctl-operator" in roles:
        rep.ok("realm role rossoctl-operator")
    else:
        rep.fail(
            "realm role rossoctl-operator",
            "absent from the token — /deploy will fail with a 502 wrapping a 403",
        )


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
                    help="comma-separated workload namespaces (default: %(default)s)")
    ap.add_argument("--gateway", default="http", help="kind only: Gateway name (default: %(default)s)")
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

    rep = Report(quiet=args.json)
    if not args.json:
        print(f"AutoBench preflight — platform={platform} context={context or '(current)'} "
              f"namespace={args.namespace}")

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
        collector = check_collector(rep, cluster, args.namespace)
        check_mlflow(rep, cluster, platform, args.namespace, collector)
        check_ingress(rep, cluster, platform, args.namespace, args.gateway)
        check_instance_config(rep, cluster, args.namespace, platform, collector, instances)
        iss_hint = next((cfg.get("iss") for cfg in (instances or {}).values() if cfg.get("iss")), None)
        check_identity(rep, iss_hint)
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
