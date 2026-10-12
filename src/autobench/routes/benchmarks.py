from fastapi import APIRouter, Depends, HTTPException, Request

from ..benchmarks.registry import (
    BENCHMARKS,
    BenchmarkDefinition,
    LLMConfigError,
    agent_name,
    build_agent_request,
    build_tool_request,
    effective_llm,
    require_llm_base,
    resolve_model,
    tool_name,
)
from ..context import RequestContext
from ..deps import bound_rossoctl_client, require_caller_jwt
from ..models import (
    DEFAULT_GATEWAY_LABEL,
    DEFAULT_KEY_SECRET,
    DEFAULT_KEY_SECRET_KEY,
    BenchmarkStatusResponse,
    BenchmarkSummary,
    DeployBenchmarkRequest,
    DeployBenchmarkResponse,
    DeploymentLLM,
    parse_key_secret,
)
from ..rossoctl.client import RossoctlClient, RossoctlError

router = APIRouter(prefix="/benchmarks", tags=["benchmarks"])


def _http_error(exc: RossoctlError) -> HTTPException:
    if exc.status_code in (404, 409):
        return HTTPException(status_code=exc.status_code, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


def _definition(name: str) -> BenchmarkDefinition:
    defn = BENCHMARKS.get(name)
    if defn is None:
        raise HTTPException(status_code=404, detail=f"unknown benchmark: {name}")
    return defn


def _summary(defn: BenchmarkDefinition) -> BenchmarkSummary:
    return BenchmarkSummary(
        name=defn.name,
        mcp_image=defn.mcp_image,
        agents=sorted(defn.agents),
        default_model=defn.default_model,
    )


@router.get("", dependencies=[Depends(require_caller_jwt)])
async def list_benchmarks() -> dict:
    return {"items": [_summary(d).model_dump() for d in BENCHMARKS.values()]}


@router.get("/{name}", dependencies=[Depends(require_caller_jwt)])
async def get_benchmark(name: str) -> dict:
    return _definition(name).model_dump()


@router.post("/{name}/deploy", status_code=201, response_model=DeployBenchmarkResponse)
async def deploy_benchmark(
    name: str,
    req: DeployBenchmarkRequest,
    request: Request,
    client: RossoctlClient = Depends(bound_rossoctl_client),
    ctx: RequestContext = Depends(require_caller_jwt),
) -> DeployBenchmarkResponse:
    defn = _definition(name)
    if req.agent not in defn.agents:
        raise HTTPException(
            status_code=404, detail=f"unknown agent '{req.agent}' for benchmark '{name}'"
        )
    if req.plugin_config_file:
        raise HTTPException(
            status_code=422,
            detail=(
                "plugin_config_file is a local filesystem path with no HTTP analog and is not "
                "supported by this Service. Use plugin_preset (auth-only|ibac-only|full) with "
                "optional per-plugin plugins=[\"NAME:POLICY\"] and on_error overrides instead."
            ),
        )
    if (req.plugin_preset or req.plugins or req.on_error) and not req.authbridge_enabled:
        raise HTTPException(
            status_code=422,
            detail=(
                "plugin_preset/plugins/on_error require authbridge_enabled=true: the AuthBridge "
                "sidecar must be injected for its plugin pipeline to take effect."
            ),
        )
    # A deployment on another gateway must be named, or its rows could not be told apart from the
    # instance gateway's in a cost comparison — and the URL itself is never published.
    if req.llm_api_base and not req.llm_gateway:
        raise HTTPException(
            status_code=422,
            detail="llm_api_base requires llm_gateway: a label the artifacts publish in its place",
        )
    llm = effective_llm(ctx.instance.workload_llm, req.llm_api_base)
    # Reject a missing LLM gateway here rather than letting the workload deploy and then die on an
    # opaque model-health timeout inside the agent. There is no default base by design.
    try:
        require_llm_base(llm)
    except LLMConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    key_secret = parse_key_secret(req.llm_key_secret) if req.llm_key_secret else None
    gateway = req.llm_gateway or (
        (ctx.instance.workload_llm.gateway_label if ctx.instance.workload_llm else None)
        or DEFAULT_GATEWAY_LABEL
    )
    tool_req = build_tool_request(
        defn, req.namespace, req.model, llm, key_secret=key_secret, gateway=gateway
    )
    agent_req = build_agent_request(
        defn,
        req.agent,
        req.namespace,
        req.model,
        req.experiment,
        ctx.instance.workload_otel,
        req.authbridge_enabled,
        llm,
        req.plugin_preset,
        req.plugins,
        req.on_error,
        ctx.instance.workload_agent_runner,
        key_secret=key_secret,
        gateway=gateway,
    )
    secret_name, secret_key = key_secret or (DEFAULT_KEY_SECRET, DEFAULT_KEY_SECRET_KEY)
    deployed_llm = DeploymentLLM(
        gateway=gateway,
        model=resolve_model(defn, req.model, llm),
        key_secret=f"{secret_name}:{secret_key}",
    )
    try:
        tool_resp = await client.create_tool(tool_req.to_rossoctl_body())
        agent_resp = await client.create_agent(agent_req.to_rossoctl_body())
    except RossoctlError as exc:
        raise _http_error(exc) from exc
    request.app.state.deployments.put(
        (ctx.instance.iss, req.namespace, name, req.agent, req.experiment), deployed_llm
    )
    return DeployBenchmarkResponse(
        benchmark=name,
        namespace=req.namespace,
        tool_name=tool_req.name,
        agent_name=agent_req.name,
        tool=tool_resp,
        agent=agent_resp,
        llm=deployed_llm,
    )


def _ready(resource: dict) -> bool:
    return resource.get("readyStatus") == "Ready"


@router.get("/{name}/status", response_model=BenchmarkStatusResponse)
async def benchmark_status(
    name: str,
    namespace: str = "team1",
    agent: str = "tool_calling",
    experiment: str = "default",
    client: RossoctlClient = Depends(bound_rossoctl_client),
) -> BenchmarkStatusResponse:
    _definition(name)
    t_name = tool_name(name)
    a_name = agent_name(name, agent, experiment)
    try:
        tool = await client.get_tool(namespace, t_name)
        agent_res = await client.get_agent(namespace, a_name)
    except RossoctlError as exc:
        raise _http_error(exc) from exc
    return BenchmarkStatusResponse(
        benchmark=name,
        namespace=namespace,
        tool_name=t_name,
        agent_name=a_name,
        tool_ready=_ready(tool),
        agent_ready=_ready(agent_res),
        tool_status=tool.get("readyStatus"),
        agent_status=agent_res.get("readyStatus"),
    )


@router.delete("/{name}/deploy", status_code=204)
async def teardown_benchmark(
    name: str,
    request: Request,
    namespace: str = "team1",
    agent: str = "tool_calling",
    experiment: str = "default",
    client: RossoctlClient = Depends(bound_rossoctl_client),
    ctx: RequestContext = Depends(require_caller_jwt),
) -> None:
    _definition(name)
    request.app.state.deployments.drop((ctx.instance.iss, namespace, name, agent, experiment))
    try:
        await client.delete_agent(namespace, agent_name(name, agent, experiment))
        await client.delete_tool(namespace, tool_name(name))
    except RossoctlError as exc:
        raise _http_error(exc) from exc
