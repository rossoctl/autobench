"""Covers the OpenShift MLflow credential the installer owns: `reference/ocp-mlflow.sh`.

`autobench-install.sh` creates sa/mlflow-reader and its token Secret when they are missing, and
`autobench-uninstall.sh` must remove exactly those — never an account that was there first, and never
a RoleBinding (ykt3's hand-made team1/mlflow-trace-writers also grants the collector). Ownership is
the record cm/autobench-mlflow-credential. Both halves run against a fake `kubectl` holding a small
object store, whose token controller fills a token Secret the way the real one does.
"""

import json
import os
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "reference" / "ocp-mlflow.sh"
SA, TOKEN, RECORD = "mlflow-reader", "mlflow-reader-token", "autobench-mlflow-credential"

FAKE_KUBECTL = r'''#!/usr/bin/env python3
"""A kubectl that knows objects by (kind, name), mints uids, and fills service-account tokens."""
import json, os, sys
state_path = os.environ["FAKE_STATE"]
state = json.load(open(state_path))
args = sys.argv[1:]
with open(state_path + ".log", "a") as log:
    log.write(" ".join(args) + "\n")
while args and args[0] in ("--context", "-n"):
    args = args[2:]
KINDS = {"sa": "serviceaccount", "serviceaccount": "serviceaccount", "secret": "secret",
         "cm": "configmap", "configmap": "configmap"}
objs = state["objects"]
def save():
    json.dump(state, open(state_path, "w"))
def put(kind, obj):
    state["next"] += 1
    obj.setdefault("metadata", {})["uid"] = f"uid-{state['next']}"
    if (kind == "secret" and obj.get("type") == "kubernetes.io/service-account-token"
            and f"serviceaccount/{obj['metadata']['annotations']['kubernetes.io/service-account.name']}" in objs):
        obj["data"] = {"token": "ZmFrZQ=="}
    objs[f"{kind}/{obj['metadata']['name']}"] = obj
verb = args[0]
if verb == "get" and args[1] == "secret" and args[2] == "-l":
    if state.get("release"):
        print("secret/sh.helm.release.v1.autobench.v1")
elif verb == "get" and args[1] == "pods":
    print(json.dumps({"items": state.get("pods", [])}))
elif verb == "get" and args[1] == "rolebindings,clusterrolebindings":
    print(json.dumps({"items": state.get("bindings", [])}))
elif verb == "get":
    obj = objs.get(f"{KINDS[args[1]]}/{args[2]}")
    if obj is None:
        sys.exit(1)
    print(json.dumps(obj))
elif verb == "create" and args[1] == "serviceaccount":
    put("serviceaccount", {"metadata": {"name": args[2]}})
elif verb == "create" and args[1] == "-f":
    obj = json.load(open(args[2]))
    put(KINDS[obj["kind"].lower()], obj)
elif verb == "patch":
    obj = objs[f"{KINDS[args[1]]}/{args[2]}"]
    obj.setdefault("data", {}).update(json.loads(args[args.index("-p") + 1])["data"])
elif verb == "delete":
    objs.pop(f"{KINDS[args[1]]}/{args[2]}", None)
    state.setdefault("deleted", []).append(f"{KINDS[args[1]]}/{args[2]}")
else:
    sys.exit(f"fake kubectl: unhandled {args}")
save()
'''


def _sa(uid):
    return {"metadata": {"name": SA, "uid": uid}}


def _token(uid, account=SA):
    return {"type": "kubernetes.io/service-account-token", "data": {"token": "ZmFrZQ=="},
            "metadata": {"name": TOKEN, "uid": uid,
                         "annotations": {"kubernetes.io/service-account.name": account}}}


def _record(**owns):
    data = {"owner": "autobench-install.sh", "installedAt": "2026-10-03T00:00:00Z",
            "serviceAccount": SA, "tokenSecret": TOKEN}
    data.update({f"owns.{k}": v for k, v in owns.items()})
    return {"kind": "ConfigMap", "metadata": {"name": RECORD, "uid": "uid-record"}, "data": data}


@pytest.fixture
def cluster(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "kubectl").write_text(FAKE_KUBECTL)
    (bindir / "kubectl").chmod(0o755)
    state = tmp_path / "state.json"

    def make(sa=None, token=None, record=None, **extra):
        objects = {}
        if sa is not None:
            objects[f"serviceaccount/{SA}"] = sa
        if token is not None:
            objects[f"secret/{TOKEN}"] = token
        if record is not None:
            objects[f"configmap/{RECORD}"] = record
        state.write_text(json.dumps({"objects": objects, "next": 0, **extra}))

    def run(cmd, *flags, **env):
        environ = {k: v for k, v in os.environ.items() if not k.startswith("MLFLOW_")}
        environ.pop("INSTALL_MLFLOW", None)
        environ.update(PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}", FAKE_STATE=str(state), **env)
        p = subprocess.run([str(SCRIPT), cmd, "--context", "ocp-test", *flags], env=environ,
                           capture_output=True, text=True, timeout=60)
        return p, json.loads(state.read_text())

    return make, run


# --- plan --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("given, env, expected", [
    ({}, {}, "install"),
    ({"sa": _sa("a")}, {}, "install"),
    ({"sa": _sa("a"), "token": _token("b")}, {}, "reuse"),
    ({"sa": _sa("a"), "token": _token("b")}, {"INSTALL_MLFLOW": "always"}, "install"),
    ({"record": _record(serviceaccount="a")}, {}, "owned"),
    ({}, {"INSTALL_MLFLOW": "never"}, "skip"),
    ({}, {"MLFLOW_BEARER_FILE": "/x"}, "skip"),
    ({}, {"MLFLOW_NO_AUTH": "1"}, "skip"),
])
def test_plan(cluster, given, env, expected):
    make, run = cluster
    make(**given)
    p, _ = run("plan", **env)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == expected


# --- install -----------------------------------------------------------------------------------------

def test_a_fresh_install_creates_both_and_records_their_uids(cluster):
    make, run = cluster
    make()
    p, state = run("install")
    assert p.returncode == 0, p.stderr
    objs = state["objects"]
    rec = objs[f"configmap/{RECORD}"]["data"]
    assert rec["owns.serviceaccount"] == objs[f"serviceaccount/{SA}"]["metadata"]["uid"]
    assert rec["owns.secret"] == objs[f"secret/{TOKEN}"]["metadata"]["uid"]
    assert objs[f"secret/{TOKEN}"]["metadata"]["annotations"]["kubernetes.io/service-account.name"] == SA
    assert "holds a token" in p.stderr
    assert "ZmFrZQ" not in p.stdout + p.stderr


def test_install_owns_only_what_was_missing(cluster):
    make, run = cluster
    make(sa=_sa("theirs"))
    p, state = run("install")
    assert p.returncode == 0, p.stderr
    rec = state["objects"][f"configmap/{RECORD}"]["data"]
    assert "owns.serviceaccount" not in rec and "owns.secret" in rec
    assert state["objects"][f"serviceaccount/{SA}"]["metadata"]["uid"] == "theirs"


def test_both_present_are_reused_and_not_recorded(cluster):
    make, run = cluster
    make(sa=_sa("a"), token=_token("b"))
    p, state = run("install")
    assert p.returncode == 0, p.stderr
    assert f"configmap/{RECORD}" not in state["objects"]
    assert "reused" in p.stderr


def test_always_takes_over_what_is_already_there(cluster):
    make, run = cluster
    make(sa=_sa("a"), token=_token("b"))
    p, state = run("install", INSTALL_MLFLOW="always")
    assert p.returncode == 0, p.stderr
    rec = state["objects"][f"configmap/{RECORD}"]["data"]
    assert rec["owns.serviceaccount"] == "a" and rec["owns.secret"] == "b"


def test_a_token_minted_for_another_account_is_refused(cluster):
    make, run = cluster
    make(sa=_sa("a"), token=_token("b", account="someone-else"))
    p, _ = run("install")
    assert p.returncode != 0
    assert "someone-else" in p.stderr


def test_a_dry_run_install_writes_nothing(cluster):
    make, run = cluster
    make()
    p, state = run("install", "--dry-run")
    assert p.returncode == 0, p.stderr
    assert state["objects"] == {}
    assert "[dry-run]" in p.stderr


# --- uninstall ---------------------------------------------------------------------------------------

def test_with_no_record_nothing_is_removed(cluster):
    make, run = cluster
    make(sa=_sa("a"), token=_token("b"))
    p, state = run("uninstall")
    assert p.returncode == 0, p.stderr
    assert "deleted" not in state
    assert "not created by autobench-install.sh" in p.stderr


def test_an_owned_credential_is_removed_with_its_record(cluster):
    make, run = cluster
    make(sa=_sa("a"), token=_token("b"), record=_record(serviceaccount="a", secret="b"))
    p, state = run("uninstall")
    assert p.returncode == 0, p.stderr
    assert state["deleted"] == [f"secret/{TOKEN}", f"serviceaccount/{SA}", f"configmap/{RECORD}"]
    assert state["objects"] == {}


def test_an_install_then_uninstall_round_trips(cluster):
    make, run = cluster
    make()
    assert run("install")[0].returncode == 0
    p, state = run("uninstall")
    assert p.returncode == 0, p.stderr
    assert state["objects"] == {}


def test_an_account_there_before_the_install_is_kept(cluster):
    make, run = cluster
    make(sa=_sa("theirs"), token=_token("b"), record=_record(secret="b"))
    p, state = run("uninstall")
    assert p.returncode == 0, p.stderr
    assert f"serviceaccount/{SA}" in state["objects"]
    assert f"secret/{TOKEN}" not in state["objects"]


def test_an_object_re_created_since_the_install_is_kept(cluster):
    make, run = cluster
    make(sa=_sa("new"), token=_token("b"), record=_record(serviceaccount="old", secret="b"))
    p, state = run("uninstall")
    assert p.returncode == 0, p.stderr
    assert state["objects"][f"serviceaccount/{SA}"]["metadata"]["uid"] == "new"
    assert "uid differs" in p.stderr


def test_a_pod_running_as_the_account_refuses(cluster):
    make, run = cluster
    make(sa=_sa("a"), token=_token("b"), record=_record(serviceaccount="a", secret="b"),
         pods=[{"metadata": {"name": "p1"}, "spec": {"serviceAccountName": SA}}])
    p, state = run("uninstall")
    assert p.returncode != 0
    assert "p1" in p.stderr and "deleted" not in state


def test_a_live_release_refuses(cluster):
    make, run = cluster
    make(sa=_sa("a"), token=_token("b"), record=_record(serviceaccount="a", secret="b"), release=True)
    p, state = run("uninstall")
    assert p.returncode != 0
    assert "uninstall the release first" in p.stderr and "deleted" not in state


def test_a_live_release_only_warns_on_a_dry_run(cluster):
    make, run = cluster
    make(sa=_sa("a"), token=_token("b"), record=_record(serviceaccount="a", secret="b"), release=True)
    p, state = run("uninstall", "--dry-run")
    assert p.returncode == 0, p.stderr
    assert "a real run removes it first" in p.stderr and "deleted" not in state


def test_a_binding_naming_the_account_is_reported_and_never_deleted(cluster):
    # ykt3's team1/mlflow-trace-writers binds this account AND the collector's — not ours to remove.
    binding = {"kind": "RoleBinding", "metadata": {"name": "mlflow-trace-writers", "namespace": "team1"},
               "subjects": [{"kind": "ServiceAccount", "name": SA, "namespace": "rossoctl-system"},
                            {"kind": "ServiceAccount", "name": "otel-collector", "namespace": "rossoctl-system"}]}
    make, run = cluster
    make(sa=_sa("a"), token=_token("b"), record=_record(serviceaccount="a", secret="b"), bindings=[binding])
    p, state = run("uninstall")
    assert p.returncode == 0, p.stderr
    assert "RoleBinding/team1/mlflow-trace-writers" in p.stderr
    assert state["bindings"] == [binding]
    assert not any("rolebinding" in d for d in state["deleted"])


def test_the_charts_own_binding_is_named_as_the_releases_not_warned(cluster):
    # Only a dry run sees it — a real uninstall removes the release before this step.
    chart = {"kind": "RoleBinding",
             "metadata": {"name": "autobench-service-mlflow-trace-writer", "namespace": "team1",
                          "annotations": {"meta.helm.sh/release-name": "autobench",
                                          "meta.helm.sh/release-namespace": "rossoctl-system"}},
             "subjects": [{"kind": "ServiceAccount", "name": SA, "namespace": "rossoctl-system"}]}
    make, run = cluster
    make(sa=_sa("a"), token=_token("b"), record=_record(serviceaccount="a", secret="b"),
         release=True, bindings=[chart])
    p, state = run("uninstall", "--dry-run")
    assert p.returncode == 0, p.stderr
    assert "removed with the Helm release" in p.stderr
    assert "NOT touched" not in p.stderr
