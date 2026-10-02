"""Covers the KinD MLflow read path the installer owns: `reference/kind-mlflow.sh` and preflight's view of it.

`autobench-install.sh` installs mlflow-reader and repoints the collector on KinD, and
`autobench-uninstall.sh` must undo exactly that — and nothing it did not do. Ownership is the record
cm/autobench-mlflow-install. The uninstall half is exercised against a fake `kubectl` holding a
small object store; the install half needs PyYAML and a live pod, so the KinD cycle proves it.
"""

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "reference"))

import preflight  # noqa: E402

SCRIPT = ROOT / "reference" / "kind-mlflow.sh"

# rossoctl-deps' own collector config, trimmed to what the checks read. The credentials are ${env:}
# references, exactly as the chart renders them.
PRISTINE = textwrap.dedent("""\
    exporters:
      otlphttp/mlflow:
        auth:
          authenticator: oauth2client/mlflow
        headers:
          x-mlflow-experiment-id: "0"
        traces_endpoint: http://mlflow:5000/v1/traces
    extensions:
      oauth2client/mlflow:
        client_id: ${env:MLFLOW_CLIENT_ID}
    """)
REPOINTED = textwrap.dedent("""\
    exporters:
      otlphttp/mlflow:
        headers:
          x-mlflow-experiment-id: '0'
        traces_endpoint: http://mlflow-reader.rossoctl-system.svc.cluster.local:5000/v1/traces
    """)
COLLECTOR_CMD = '["otelcol","--set","receivers::otlp::protocols::http::endpoint=0.0.0.0:8335"]'


class KindCluster:
    """Enough of a KinD cluster for check_collector / check_mlflow."""

    def __init__(self, config: str, objects: set[tuple[str, str]]):
        self.config, self.objects = config, objects

    def run(self, *args, timeout=60):
        if "deploy" in args and preflight.COLLECTOR_DEPLOY in args:
            return 0, COLLECTOR_CMD, ""
        if "otel-collector-config" in args:
            return 0, self.config, ""
        return 1, "", "not found"

    def exists(self, *args):
        return (args[-2], args[-1]) in self.objects

    def get_json(self, *args):
        return None


def _rows(rep):
    return {r["label"]: r["status"] for r in rep.rows}


def test_the_pristine_collector_target_is_named_without_its_port():
    # `http://mlflow:5000/...` once looked up a Service called `mlflow:5000` and missed the real
    # finding: that it is the OIDC-gated writer, which 401s every span while the run passes.
    cluster = KindCluster(PRISTINE, {("svc", "mlflow")})
    rep = preflight.Report(quiet=True)
    collector = preflight.check_collector(rep, cluster, "rossoctl-system")
    preflight.check_mlflow(rep, cluster, "kind", "rossoctl-system", collector)
    rows = _rows(rep)
    assert rows["MLflow svc mlflow in rossoctl-system"] == preflight.OK
    assert rows["collector does not export to the OIDC-gated mlflow"] == preflight.FAIL
    assert rows["collector MLflow export is authenticated"] == preflight.WARN


def test_an_install_that_creates_the_reader_is_not_failed_for_its_absence():
    cluster = KindCluster(PRISTINE, {("svc", "mlflow")})
    rep = preflight.Report(quiet=True)
    collector = preflight.check_collector(rep, cluster, "rossoctl-system", repointing=True)
    preflight.check_mlflow(rep, cluster, "kind", "rossoctl-system", collector, installing=True)
    assert rep.count(preflight.FAIL) == 0 and rep.count(preflight.WARN) == 0
    assert "creates it" in next(r["detail"] for r in rep.rows if r["label"] == "deploy/mlflow-reader")


def test_kind_mlflow_needs_pre_install():
    p = subprocess.run([sys.executable, str(ROOT / "reference" / "preflight.py"), "--kind-mlflow"],
                       capture_output=True, text=True)
    assert p.returncode == 2 and "--pre-install" in p.stderr


# --- kind-mlflow.sh uninstall, against a fake kubectl ------------------------------------------------

FAKE_KUBECTL = r'''#!/usr/bin/env python3
"""A kubectl that knows ConfigMaps by name and the reader by manifest; every call is logged."""
import json, os, sys
state_path = os.environ["FAKE_STATE"]
state = json.load(open(state_path))
args = sys.argv[1:]
with open(state_path + ".log", "a") as log:
    log.write(" ".join(args) + "\n")
while args and args[0] in ("--context", "-n"):
    args = args[2:]
def save():
    json.dump(state, open(state_path, "w"))
verb = args[0]
if verb == "get" and args[1] == "-f":
    if state["reader"]:
        print("service/mlflow-reader\ndeployment.apps/mlflow-reader")
elif verb == "get" and args[1] in ("cm", "configmap"):
    obj = state["cms"].get(args[2])
    if obj is None:
        sys.exit(1)
    print(json.dumps(obj))
elif verb == "get" and args[1] in ("deploy", "svc"):
    sys.exit(0 if state["reader"] and args[2] == "mlflow-reader" else 1)
elif verb == "replace":
    obj = json.load(open(args[2]))
    state["cms"][obj["metadata"]["name"]] = obj
    state["replaced"] = obj["metadata"]["name"]
elif verb == "delete" and args[1] == "-f":
    state["reader"] = False
elif verb == "delete" and args[1] == "cm":
    state["cms"].pop(args[2], None)
elif verb == "rollout":
    state.setdefault("rollouts", []).append(args[1])
else:
    sys.exit(f"fake kubectl: unhandled {args}")
save()
'''


def _sha(data: dict) -> str:
    # The script's data_sha: `jq -cS '.data // {}' | shasum -a 256 | cut -c1-8`.
    p = subprocess.run(["jq", "-cS", ".data // {}"], input=json.dumps({"data": data}),
                       capture_output=True, text=True, check=True)
    return hashlib.sha256(p.stdout.encode()).hexdigest()[:8]


def _cm(name, data, annotations=None):
    return {"apiVersion": "v1", "kind": "ConfigMap",
            "metadata": {"name": name, "namespace": "rossoctl-system",
                         "annotations": annotations or {}}, "data": data}


@pytest.fixture
def cluster(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "kubectl").write_text(FAKE_KUBECTL)
    (bindir / "kubectl").chmod(0o755)
    state = tmp_path / "state.json"

    def make(reader: bool, collector: dict, record: dict | None):
        cms = {"otel-collector-config": _cm("otel-collector-config", collector,
                                            {"kubectl.kubernetes.io/last-applied-configuration": "{}"})}
        if record is not None:
            cms["autobench-mlflow-install"] = _cm("autobench-mlflow-install", record)
        state.write_text(json.dumps({"reader": reader, "cms": cms}))

    def uninstall():
        env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
               "FAKE_STATE": str(state)}
        p = subprocess.run([str(SCRIPT), "uninstall", "--context", "kind-test",
                            "--record-dir", str(tmp_path)], env=env, capture_output=True, text=True)
        return p, json.loads(state.read_text())

    return make, uninstall, tmp_path


def _record(prior: dict, post: dict) -> dict:
    return {"owner": "autobench-install.sh", "installedAt": "2026-10-02T00:00:00Z",
            "collectorPriorSha": _sha(prior), "collectorPostSha": _sha(post),
            "collectorPrior": json.dumps(prior)}


PRIOR = {"base.yaml": PRISTINE}
POST = {"base.yaml": REPOINTED}


def test_a_reader_with_no_record_is_left_alone(cluster):
    make, uninstall, _ = cluster
    make(reader=True, collector=POST, record=None)
    p, state = uninstall()
    assert p.returncode == 0, p.stderr
    assert state["reader"] and "replaced" not in state
    assert "not installed by autobench-install.sh" in p.stderr


def test_an_owned_install_is_undone_exactly(cluster):
    make, uninstall, _ = cluster
    make(reader=True, collector=POST, record=_record(PRIOR, POST))
    p, state = uninstall()
    assert p.returncode == 0, p.stderr
    collector = state["cms"]["otel-collector-config"]
    assert collector["data"] == PRIOR
    # The annotation the repoint's `kubectl apply` added goes too.
    assert "kubectl.kubernetes.io/last-applied-configuration" not in collector["metadata"]["annotations"]
    assert state["rollouts"] == ["restart", "status"]
    assert not state["reader"] and "autobench-mlflow-install" not in state["cms"]


def test_a_collector_changed_since_the_install_is_not_clobbered(cluster):
    make, uninstall, tmp = cluster
    someone_elses = {"base.yaml": REPOINTED + "processors: {}\n"}
    make(reader=True, collector=someone_elses, record=_record(PRIOR, POST))
    p, state = uninstall()
    assert p.returncode == 0, p.stderr
    assert state["cms"]["otel-collector-config"]["data"] == someone_elses
    assert json.loads((tmp / "otel-collector-config.prior.json").read_text()) == PRIOR
    assert "NOT restored" in p.stderr
    # The reader is still ours to remove.
    assert not state["reader"] and "autobench-mlflow-install" not in state["cms"]


def test_a_collector_install_never_changed_is_not_restarted(cluster):
    make, uninstall, _ = cluster
    make(reader=True, collector=POST, record=_record(POST, POST))
    p, state = uninstall()
    assert p.returncode == 0, p.stderr
    assert "replaced" not in state and "rollouts" not in state
    assert not state["reader"]


# --- OpenShift: is the pre-installed MLflow actually there? ------------------------------------------

RHOAI = "https://mlflow.redhat-ods-applications.svc.cluster.local:8443"


class ProxyCluster:
    """A cluster whose API-server service proxy answers with one canned kubectl result."""

    def __init__(self, rc: int, err: str):
        self.rc, self.err, self.calls = rc, err, []

    def run(self, *args, timeout=60):
        self.calls.append(args)
        return self.rc, "", self.err

    def exists(self, *args):
        return True


def _answers(rc, err, url=RHOAI):
    rep = preflight.Report(quiet=True)
    cluster = ProxyCluster(rc, err)
    preflight.check_mlflow_answers(rep, cluster, "Service reads", url)
    return rep.rows[-1], cluster


# The kubectl messages below are the ones measured on ykt5, 2026-10-02.
@pytest.mark.parametrize("err, status", [
    # MLflow's own replies: a server is there.
    ("Error from server (NotFound): the server could not find the requested resource", preflight.OK),
    ("error: You must be logged in to the server (the server has asked for the client to provide "
     "credentials)", preflight.OK),
    # The API server's: nothing answering.
    ('Error from server (NotFound): services "mlflow" not found', preflight.FAIL),
    ('Error from server (ServiceUnavailable): no endpoints available for service "mlflow"', preflight.FAIL),
    ('Error from server (ServiceUnavailable): no service port 8443 found for service "mlflow"',
     preflight.FAIL),
    ("Error from server (ServiceUnavailable): error trying to reach service: EOF", preflight.FAIL),
    ("timed out after 30s", preflight.FAIL),
    # Cannot tell — never a pass, never a failure the cluster did not earn.
    ('Error from server (Forbidden): services "mlflow" is forbidden: User "u" cannot get resource '
     '"services/proxy"', preflight.WARN),
    ("error: You must be logged in to the server (Unauthorized)", preflight.WARN),
])
def test_mlflow_answers_tells_the_api_server_from_mlflow(err, status):
    row, cluster = _answers(1, err)
    assert row["status"] == status, row
    assert cluster.calls == [("get", "--raw",
                              "/api/v1/namespaces/redhat-ods-applications/services/https:mlflow:8443/proxy/")]


def test_mlflow_answers_skips_an_external_url():
    row, cluster = _answers(0, "", url="https://mlflow.apps.example.com")
    assert row["status"] == preflight.SKIP and not cluster.calls


def test_openshift_probes_the_write_and_read_targets_once_each():
    rep = preflight.Report(quiet=True)
    cluster = ProxyCluster(1, "Error from server (NotFound): the server could not find the requested resource")
    collector = {"traces_endpoint": RHOAI + "/v1/traces"}
    preflight.check_mlflow(rep, cluster, "openshift", "rossoctl-system", collector,
                           read_urls=[RHOAI, "http://other.mlflow-ns.svc:5000"])
    probes = [c for c in cluster.calls if c[:2] == ("get", "--raw")]
    # The collector's target and the first read URL are one Service: probed once.
    assert len(probes) == 2
    assert rep.count(preflight.FAIL) == 0
