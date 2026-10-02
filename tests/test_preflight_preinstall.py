"""Covers `reference/preflight.py --pre-install`: what the release itself creates is not a prerequisite.

The first real install through `autobench-install.sh` (ykt5, 2026-10-02) failed its own pre-install
preflight: the uninstall before it had removed the chart-owned trace-writer RoleBinding, and preflight
then demanded that binding exist before the chart that creates it had run. The judge's Deployment and
fields warned for the same reason. A dry-run against a still-installed release could not catch it.
"""

import os
import pathlib
import shutil
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "reference"))

import preflight  # noqa: E402

CHART = str(ROOT / "deploy" / "helm" / "autobench")
_PATH = os.environ["PATH"] + os.pathsep + str(pathlib.Path.home() / ".rd" / "bin")
needs_helm = pytest.mark.skipif(not shutil.which("helm", path=_PATH), reason="needs helm to render the chart")


class FreshCluster:
    """A cluster just after `helm uninstall`: the platform ConfigMap with empty ibac fields, and none
    of the release's objects."""

    def __init__(self, rolebindings: list | None = None):
        self.rolebindings = rolebindings or []

    def get_json(self, *args):
        if "rolebindings" in args:
            return {"items": self.rolebindings}
        if "rossoctl-platform-config" in args:
            return {"data": {"config.yaml": 'ibac:\n  judgeEndpoint: ""\n  judgeModel: ""\nother: 1\n'}}
        return None

    def exists(self, *args):
        return False

    def secret_data(self, namespace, name):
        return None


def _statuses(rep):
    return {r["label"]: r["status"] for r in rep.rows}


@pytest.fixture
def helm_on_path(monkeypatch):
    monkeypatch.setenv("PATH", _PATH)


@needs_helm
def test_the_rendered_chart_names_the_namespaces_it_binds(helm_on_path):
    ns = "rossoctl-system"
    assert preflight.chart_trace_writer_namespaces(CHART, str(ROOT / "deploy/helm/values-openshift.yaml"), ns) == {"team1"}
    # kind's values leave mlflowTraceWriter off, so the chart binds nothing.
    assert preflight.chart_trace_writer_namespaces(CHART, str(ROOT / "deploy/helm/values-kind.yaml"), ns) == set()


def test_an_unrenderable_chart_is_none_not_empty(helm_on_path, tmp_path):
    assert preflight.chart_trace_writer_namespaces(str(tmp_path / "no-chart"), None, "x") is None


@pytest.mark.parametrize("chart_binds, expect", [
    (None, preflight.FAIL),         # post-install audit: the binding must be live
    ({"team1"}, preflight.OK),      # pre-install: the chart creates it
    ({"team2"}, preflight.FAIL),    # pre-install, but the chart binds a different workspace
    (set(), preflight.FAIL),        # pre-install with mlflowTraceWriter off
])
def test_the_trace_writer_counts_the_chart_only_before_an_install(chart_binds, expect):
    rep = preflight.Report(quiet=True)
    preflight.check_mlflow_write_grant(rep, FreshCluster(), "openshift", "rossoctl-system", ["team1"],
                                       chart_binds)
    assert _statuses(rep) == {"team1: mlflow-reader may write traces": expect}


def test_a_live_binding_still_wins_before_an_install():
    rb = {"metadata": {"name": "mlflow-trace-writers"},
          "roleRef": {"kind": "ClusterRole", "name": preflight.TRACE_WRITER_CLUSTERROLE},
          "subjects": [{"kind": "ServiceAccount", "name": preflight.TRACE_WRITER_SA}]}
    rep = preflight.Report(quiet=True)
    preflight.check_mlflow_write_grant(rep, FreshCluster([rb]), "openshift", "rossoctl-system", ["team1"],
                                       set())
    assert rep.rows[0]["status"] == preflight.OK and "mlflow-trace-writers" in rep.rows[0]["detail"]


def test_a_judge_this_install_creates_is_not_missing():
    rep = preflight.Report(quiet=True)
    preflight.check_ibac_judge(rep, FreshCluster(), "rossoctl-system", required=False, installing=True)
    assert rep.count(preflight.FAIL) == 0 and rep.count(preflight.WARN) == 0
    assert "creates it" in next(r["detail"] for r in rep.rows if r["label"] == "ibac-judge Deployment")


def test_without_the_judge_its_absence_still_warns():
    rep = preflight.Report(quiet=True)
    preflight.check_ibac_judge(rep, FreshCluster(), "rossoctl-system", required=False)
    s = _statuses(rep)
    assert s["ibac.judgeEndpoint / judgeModel"] == preflight.WARN
    assert s["ibac-judge Deployment"] == preflight.WARN


def test_after_an_install_the_judge_is_strict_again():
    rep = preflight.Report(quiet=True)
    preflight.check_ibac_judge(rep, FreshCluster(), "rossoctl-system", required=True)
    assert rep.count(preflight.FAIL) >= 3 and rep.count(preflight.OK) == 0


class JudgedCluster(FreshCluster):
    def __init__(self, model: str):
        super().__init__()
        self.model = model

    def get_json(self, *args):
        if "rossoctl-platform-config" in args:
            return {"data": {"config.yaml":
                             f'ibac:\n  judgeEndpoint: "http://ibac-judge.rossoctl-system:8080"\n'
                             f'  judgeModel: "{self.model}"\n'}}
        return super().get_json(*args)


@pytest.mark.parametrize("model, expect", [
    ("Azure/gpt-4.1", preflight.OK),            # ETE's catalogue id
    ("azure/gpt-5.6-terra", preflight.OK),      # vpc-int's: its namespace segment is lowercase
    ("openai/Azure/gpt-4.1", preflight.FAIL),   # a workload (litellm client) string — 403s
])
def test_the_judge_model_rejects_only_the_client_prefix(model, expect):
    rep = preflight.Report(quiet=True)
    preflight.check_ibac_judge(rep, JudgedCluster(model), "rossoctl-system", required=True)
    assert _statuses(rep)["ibac.judgeModel"] == expect


@pytest.mark.parametrize("rewriting, expect", [(False, preflight.FAIL), (True, preflight.OK)])
def test_a_gateway_the_install_rewrites_is_not_a_failure_yet(monkeypatch, rewriting, expect):
    # KinD repointed from the intranet gateway to the internet one (2026-10-02): the old instance
    # file failed the pre-install run although the bootstrap was about to rewrite it.
    monkeypatch.setattr(preflight, "llm_profile_base", lambda p: "https://gw-b.example.com")
    inst = {"x.json": {"iss": "http://kc.example.com/realms/r",
                       "workload_llm": {"api_base": "https://gw-a.example.com", "default_model": "openai/m"}}}
    rep = preflight.Report(quiet=True)
    preflight.check_instance_config(rep, FreshCluster(), "rossoctl-system", "kind", {}, inst,
                                    llm_profile="internet", rewriting=rewriting)
    assert _statuses(rep)["x.json: LLM gateway matches the internet profile"] == expect


def _render_judge(platform: str) -> list[dict]:
    import subprocess
    import yaml
    out = subprocess.run(
        ["helm", "template", "t", CHART, "-f", str(ROOT / f"deploy/helm/values-{platform}.yaml"),
         "--set", "ibacJudge.enabled=true", "--set-string", "ibacJudge.upstreamBase=https://llm.example.com",
         "--set-string", "ibacJudge.model=m"],
        capture_output=True, text=True, check=True, env={**os.environ, "PATH": _PATH}).stdout
    return [d for d in yaml.safe_load_all(out)
            if d and d["kind"] in ("Deployment", "Job") and "judge" in d["metadata"]["name"]]


@needs_helm
@pytest.mark.parametrize("platform, uid", [("kind", 10001), ("openshift", None)])
def test_the_judge_runs_as_a_named_uid_off_openshift(platform, uid):
    # python:3.12-slim runs as root: on KinD, runAsNonRoot with no runAsUser is a
    # CreateContainerConfigError (2026-10-02). OpenShift's SCC assigns the UID, and must be let to.
    objs = _render_judge(platform)
    assert {o["kind"] for o in objs} == {"Deployment", "Job"}
    for o in objs:
        for c in o["spec"]["template"]["spec"]["containers"]:
            assert c["securityContext"]["runAsNonRoot"] is True
            assert c["securityContext"].get("runAsUser") == uid, o["metadata"]["name"]
