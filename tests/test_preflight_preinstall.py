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
