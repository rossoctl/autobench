"""Per-deploy LLM gateway, key Secret and model — for comparing gateway x model combinations.

The contract under test: a deploy may name another gateway (URL), the Secret holding its key, and
a label; the workloads get exactly those; the deploy echoes what it enacted; a run inherits the
label; and the label, never the URL, is what the published artifacts carry.
"""

import importlib.util
import json
import pathlib
import sys

import httpx
import pytest
import respx
from starlette.testclient import TestClient

from autobench import cli, s3_export
from autobench.app import create_app
from autobench.benchmarks import registry
from autobench.config import settings
from autobench.models import WorkloadLLMConfig

from conftest import JWKS_URL, ROSSOCTL_URL, TOKEN_URL
from test_runs_routes import _install_fakes, _mock_ready, _poll

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "reference"))
import costlib  # noqa: E402

CB = "https://litellm.cb.example.com"
CB_BODY = {"llm_api_base": CB, "llm_key_secret": "cb-key", "llm_gateway": "cb",
           "model": "openai/rits/zai-org/glm-5-3"}


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


def _mock_auth(jwks_doc):
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json=jwks_doc))
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "svc"}))


def _mock_create():
    tool = respx.post(f"{ROSSOCTL_URL}/api/v1/tools").mock(return_value=httpx.Response(201, json={}))
    agent = respx.post(f"{ROSSOCTL_URL}/api/v1/agents").mock(return_value=httpx.Response(201, json={}))
    return tool, agent


def _env(route):
    return json.loads(route.calls.last.request.content)["envVars"]


def _secret_refs(env):
    return {e["name"]: (e["valueFrom"]["secretKeyRef"]["name"], e["valueFrom"]["secretKeyRef"]["key"])
            for e in env if e.get("valueFrom")}


@pytest.fixture
def client(tmp_path, instance_dict, monkeypatch):
    (tmp_path / "kc.json").write_text(json.dumps(instance_dict))
    monkeypatch.setattr(settings, "instances_dir", str(tmp_path))
    with TestClient(create_app()) as c:
        yield c


# --- registry ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("bench", sorted(registry.BENCHMARKS))
def test_key_rebinding_leaves_no_openai_secret_reference(bench):
    defn = registry.BENCHMARKS[bench]
    agent = sorted(defn.agents)[0]
    llm = WorkloadLLMConfig(api_base=CB)
    tool = registry.build_tool_request(defn, "team1", None, llm, key_secret=("cb-key", "k"), gateway="cb")
    ag = registry.build_agent_request(defn, agent, "team1", None, llm=llm,
                                      key_secret=("cb-key", "k"), gateway="cb")
    for req in (tool, ag):
        env = [e.to_wire() for e in req.env_vars]
        refs = _secret_refs(env)
        assert all(name != "openai-secret" for name, _ in refs.values()), refs
        if "OPENAI_API_KEY" in refs:
            assert refs["OPENAI_API_KEY"] == ("cb-key", "k")
        assert {e["name"]: e.get("value") for e in env}["AUTOBENCH_LLM_GATEWAY"] == "cb"
    assert ("cb-key", "k") in registry.required_secrets(defn, agent, ("cb-key", "k")) or not any(
        n == "openai-secret" for n, _ in registry.required_secrets(defn, agent))


def test_effective_llm_replaces_only_the_base():
    inst = WorkloadLLMConfig(api_base="https://a.example.com", default_model="m",
                             disable_proxy=True, no_proxy="x")
    eff = registry.effective_llm(inst, CB)
    assert (eff.api_base, eff.default_model, eff.disable_proxy, eff.no_proxy) == (CB, "m", True, "x")
    assert registry.effective_llm(inst, None) is inst
    assert inst.api_base == "https://a.example.com"  # the instance's own config is untouched


# --- deploy route -----------------------------------------------------------------------------------


@respx.mock
def test_deploy_on_another_gateway_enacts_and_echoes_it(client, make_token, jwks_doc):
    _mock_auth(jwks_doc)
    tool_route, agent_route = _mock_create()
    r = client.post("/benchmarks/tau2/deploy", headers=_auth(make_token), json=CB_BODY)
    assert r.status_code == 201, r.text
    assert r.json()["llm"] == {"gateway": "cb", "model": "openai/rits/zai-org/glm-5-3",
                               "key_secret": "cb-key:apikey"}
    for route in (tool_route, agent_route):
        env = _env(route)
        values = {e["name"]: e.get("value") for e in env}
        assert values["OPENAI_API_BASE"] == CB
        assert values["AUTOBENCH_LLM_GATEWAY"] == "cb"
        assert _secret_refs(env)["OPENAI_API_KEY"] == ("cb-key", "apikey")
    agent_env = {e["name"]: e.get("value") for e in _env(agent_route)}
    assert agent_env["LLM_API_BASE"] == CB and agent_env["LLM_MODEL"] == CB_BODY["model"]
    # tau2's user simulator follows the agent onto the same gateway and model
    tool_env = {e["name"]: e.get("value") for e in _env(tool_route)}
    assert tool_env["EXGENTIC_SET_BENCHMARK_USER_SIMULATOR_MODEL"] == CB_BODY["model"]


@respx.mock
def test_default_deploy_echoes_the_instance_gateway(client, make_token, jwks_doc):
    _mock_auth(jwks_doc)
    _mock_create()
    r = client.post("/benchmarks/gsm8k/deploy", headers=_auth(make_token), json={})
    assert r.status_code == 201, r.text
    assert r.json()["llm"]["gateway"] == "default"
    assert r.json()["llm"]["key_secret"] == "openai-secret:apikey"


@respx.mock
def test_instance_gateway_label_is_the_default(tmp_path, instance_dict, monkeypatch, make_token, jwks_doc):
    instance_dict["workload_llm"]["gateway_label"] = "ete-ext"
    (tmp_path / "kc.json").write_text(json.dumps(instance_dict))
    monkeypatch.setattr(settings, "instances_dir", str(tmp_path))
    _mock_auth(jwks_doc)
    _mock_create()
    with TestClient(create_app()) as c:
        r = c.post("/benchmarks/gsm8k/deploy", headers=_auth(make_token), json={})
    assert r.json()["llm"]["gateway"] == "ete-ext"


@pytest.mark.parametrize("body,needle", [
    ({"llm_api_base": CB}, "llm_gateway"),                       # a URL must be named
    ({"llm_api_base": "ftp://x", "llm_gateway": "cb"}, "http"),  # not a URL we dial
    ({"llm_key_secret": "Bad_Name"}, "Secret name"),
    ({"llm_gateway": "has space"}, "llm_gateway"),
    ({"llm_api_bse": CB}, "extra"),                               # a typo is refused, not ignored
])
@respx.mock
def test_deploy_rejects(client, make_token, jwks_doc, body, needle):
    _mock_auth(jwks_doc)
    tool_route, _ = _mock_create()
    r = client.post("/benchmarks/gsm8k/deploy", headers=_auth(make_token), json=body)
    assert r.status_code == 422, r.text
    assert needle.lower() in r.text.lower()
    assert not tool_route.called


# --- runs and export --------------------------------------------------------------------------------


@respx.mock
def test_run_inherits_the_deployments_gateway(client, make_token, jwks_doc, monkeypatch):
    _mock_auth(jwks_doc)
    _mock_create()
    assert client.post("/benchmarks/gsm8k/deploy", headers=_auth(make_token), json=CB_BODY).status_code == 201
    _mock_ready(True)
    _install_fakes(monkeypatch)
    r = client.post("/benchmarks/gsm8k/runs", headers=_auth(make_token), json={})
    data = _poll(client, _auth(make_token), r.json()["run_id"])
    assert data["llm"] == {"gateway": "cb", "model": CB_BODY["model"], "key_secret": "cb-key:apikey"}
    assert CB not in json.dumps(data)


@respx.mock
def test_not_ready_names_the_rebound_secret(client, make_token, jwks_doc, monkeypatch):
    _mock_auth(jwks_doc)
    _mock_create()
    client.post("/benchmarks/gsm8k/deploy", headers=_auth(make_token), json=CB_BODY)
    _mock_ready(False)
    _install_fakes(monkeypatch)
    r = client.post("/benchmarks/gsm8k/runs", headers=_auth(make_token), json={})
    assert r.status_code == 424
    assert "cb-key" in r.json()["detail"] and "openai-secret" not in r.json()["detail"]


@respx.mock
def test_teardown_forgets_the_deployment(client, make_token, jwks_doc, monkeypatch):
    _mock_auth(jwks_doc)
    _mock_create()
    client.post("/benchmarks/gsm8k/deploy", headers=_auth(make_token), json=CB_BODY)
    respx.delete(url__regex=rf"{ROSSOCTL_URL}/api/v1/(agents|tools)/.*").mock(
        return_value=httpx.Response(204))
    assert client.delete("/benchmarks/gsm8k/deploy", headers=_auth(make_token)).status_code == 204
    _mock_ready(True)
    _install_fakes(monkeypatch)
    r = client.post("/benchmarks/gsm8k/runs", headers=_auth(make_token), json={})
    assert _poll(client, _auth(make_token), r.json()["run_id"])["llm"] is None


async def test_export_stamps_the_label_on_every_row(monkeypatch):
    seen = {}

    def fake_sync(cfg, **kw):
        seen.update(kw)
        return []

    monkeypatch.setattr(s3_export, "_export_sync", fake_sync)

    class Rec:
        def model_dump(self, mode=None):
            return {"task_id": "0", "model": "m", "llm_input_tokens": 3, "llm_output_tokens": 4}

    from autobench.models import S3Config
    await s3_export.export_run(S3Config(bucket="b"), preferred_username="u", source_iss="https://i/r",
                               benchmark="gsm8k", run_id="r1", records=[Rec()], run_summary={},
                               llm_gateway="cb")
    assert seen["record_dicts"][0]["llm_gateway"] == "cb"
    assert s3_export._token_rows(seen["record_dicts"])[0]["llm_gateway"] == "cb"


# --- CLI --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("argv", [
    ["all", "--no-deploy", "--llm-gateway", "cb"],
    ["run", "--model", "m"],
    ["all", "--llm-api-base", CB],
])
def test_cli_refuses_inert_or_unnamed_overrides(argv, monkeypatch):
    monkeypatch.setenv("BM_BASE", "http://x")
    monkeypatch.setenv("BM_ISS", "http://y")
    with pytest.raises(SystemExit) as e:
        cli.main(argv)
    assert e.value.code == 1


class _FakeClient:
    def __init__(self, echo):
        self.echo, self.body = echo, None

    def json_api(self, path, data=None, method=None, **kw):
        self.body = data
        return self.echo


def _args(**kw):
    base = dict(benchmark="gsm8k", agent="tool_calling", namespace="team1", experiment="default",
                model=None, preset=None, plugin=None, on_error=None,
                llm_api_base=None, llm_key_secret=None, llm_gateway=None)
    base.update(kw)
    return type("A", (), base)()


def test_cli_stops_when_an_old_service_ignores_the_gateway():
    with pytest.raises(SystemExit) as e:
        cli.do_deploy(_FakeClient({}), _args(llm_api_base=CB, llm_gateway="cb"))
    assert e.value.code == 3


def test_cli_sends_the_overrides_and_accepts_a_matching_echo():
    fc = _FakeClient({"llm": {"gateway": "cb", "model": "m", "key_secret": "cb-key:apikey"}})
    cli.do_deploy(fc, _args(llm_api_base=CB, llm_gateway="cb", llm_key_secret="cb-key", model="m"))
    assert fc.body["llm_api_base"] == CB and fc.body["llm_key_secret"] == "cb-key"


# --- cost + run-12 ----------------------------------------------------------------------------------


def test_costlib_prices_a_carded_gateway_only_from_its_own_card(monkeypatch):
    monkeypatch.setitem(costlib.GATEWAYS, "cb", {"models": {"m": {"input_per_1m": 1, "output_per_1m": 2}}})
    assert costlib.cost("m", 1e6, 1e6, "cb") == 3
    assert costlib.cost("openai/Azure/gpt-4.1", 1e6, 0, "cb") is None   # never borrowed
    assert costlib.cost("openai/Azure/gpt-4.1", 1e6, 0, "elsewhere") == 2.0
    assert costlib.cost("openai/Azure/gpt-4.1", 1e6, 0) == 2.0
    assert costlib.priced([{"model": "openai/Azure/gpt-4.1", "llm_input_tokens": 1,
                            "llm_gateway": "cb"}]) == []


def _load_run12(monkeypatch, **env):
    monkeypatch.setenv("BM_BASE", "http://x")
    monkeypatch.setenv("BM_ISS", "http://y/realms/r")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("run12", ROOT / "reference" / "run-12.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_run12_puts_every_leg_on_the_gateway_and_never_records_the_url(monkeypatch):
    m = _load_run12(monkeypatch, BM_LLM_API_BASE=CB, BM_LLM_GATEWAY="cb",
                    BM_LLM_KEY_SECRET="cb-key", BM_MODEL_DEFAULT="openai/rits/zai-org/glm-5-3")
    specs = json.loads((ROOT / "reference" / "run12_specs.json").read_text())
    m.apply_gateway(specs)
    for s in specs:
        db = s["deploy_body"]
        assert (db["llm_gateway"], db["llm_api_base"], db["model"]) == ("cb", CB, "openai/rits/zai-org/glm-5-3")
        assert "llm_api_base" not in m.public_deploy_request(db)
        assert m.cache_group(s).startswith("cb:")


def test_run12_refuses_a_gateway_without_a_label_or_model(monkeypatch):
    m = _load_run12(monkeypatch, BM_LLM_API_BASE=CB)
    with pytest.raises(SystemExit):
        m.apply_gateway([{"n": 1, "bench": "gsm8k", "deploy_body": {}}])


def test_compare_groups_by_gateway_and_model(tmp_path):
    spec = importlib.util.spec_from_file_location("ggc", ROOT / "reference" / "gen-gateway-compare.py")
    ggc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ggc)
    for gw, model, run in (("cb", "m1", "r1"), ("default", "openai/Azure/gpt-4.1", "r2")):
        d = tmp_path / run
        d.mkdir()
        (d / "run.json").write_text(json.dumps({"run_id": run, "llm": {"gateway": gw, "model": model}}))
        rows = [{"benchmark_name": "gsm8k", "model": model, "llm_gateway": gw, "llm_input_tokens": 100,
                 "llm_output_tokens": 10, "evaluation_result": True, "total_latency_s": 2.0},
                {"benchmark_name": "gsm8k", "model": model, "llm_gateway": gw, "llm_input_tokens": 0,
                 "evaluation_result": None}]
        (d / "report.ndjson").write_text("\n".join(json.dumps(r) for r in rows))
    out = {s["gateway"]: s for s in (ggc.summarize(k, g) for k, g in ggc.group(ggc.load_runs([tmp_path])).items())}
    assert out["cb"]["tasks"] == 2 and out["cb"]["lost"] == 1 and out["cb"]["pass"] == 0.5
    assert out["cb"]["per_task"] is None                      # cb has no rates yet: unpriced
    assert out["default"]["per_task"] == pytest.approx((100 * 2 + 10 * 8) / 1e6)
    assert "unpriced" in ggc.table(list(out.values()))


def _load_gen(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "reference" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_gateway_legs_block_prices_cb_at_zero_and_names_no_url():
    g = _load_gen("gen-gateway-legs")
    def leg(bench, model, cost, rows=5, att=5):
        return {"bench": bench, "run_id": "20261012000000-abcdef01", "models": [model], "pass": 1.0,
                "attempted": att, "rows": rows, "tin": 1000, "tout": 100, "wall": 10.0,
                "cost": cost, "unpriced": 0}
    A = {1: leg("gsm8k", "openai/Azure/gpt-5-mini-2025-08-07", 0.0004),
         2: leg("tau2", "openai/aws/claude-sonnet-5", 1.6),
         3: leg("appworld", "openai/gemini-2.5-pro", 2.1, rows=4)}
    B = {n: {**v, "models": ["openai/rits/zai-org/glm-5-3"], "cost": 0.0} for n, v in A.items()}
    B[3]["rows"] = 3
    text = g.block(A, B, "ETE-Ext", "cb", "t1", "t2", {})
    assert "| **$0.00** |" in text and "https://" not in text
    assert "appworld lost 1 of 5 tasks that way on ETE-Ext and 2 of 5 on cb" in text


def test_cb_models_carry_a_zero_rate_card():
    for m in ("openai/rits/zai-org/glm-5-3",
              "openai/rits/nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-NVFP4"):
        assert costlib.cost(m, 1e6, 1e6, "cb") == 0.0
