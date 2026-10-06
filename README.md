# AutoBench

Automated benchmarking of agentic AI workloads on Rossoctl. AutoBench deploys a benchmark workload —
a tool-calling agent paired with the benchmark's MCP server — runs it, collects per-task telemetry,
and publishes the results as a set of durable artifacts.

It is a pure-Python HTTP service — no `kubectl`, no shelling out. Callers authenticate with their own
bearer token, which AutoBench uses for attribution and routing; it performs its own ROPC login to
Rossoctl over `httpx`.

## Components

| Component | What it is |
|---|---|
| `autobench-service` | The service. FastAPI, one per-issuer instance config, deployed on the cluster whose workloads it benchmarks. |
| `autobench-cli` | Stdlib-only client CLI that drives the service over its HTTP API end to end. |

## Benchmarks

**gsm8k** (single-turn arithmetic reasoning — the smoke test), **tau2** (multi-turn dialogue with a
server-side user simulator), and **appworld** (long-horizon app automation). See
[docs/BENCHMARKS_PRIMER.md](docs/BENCHMARKS_PRIMER.md) — including
[which one to pick](docs/BENCHMARKS_PRIMER.md#picking-a-benchmark) and
[what a run of it costs](docs/BENCHMARKS_PRIMER.md#what-a-run-costs) in tokens and minutes, measured.
They are not interchangeable: a tau2 task costs **175×** the tokens of a gsm8k task and an appworld
task **621×** — and in dollars the gap is wider still, 282× and 1,195×, because each rung also runs a
dearer model.

## Getting started

[docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md) walks from deploy to result analysis with real,
copy-pasteable commands. [docs/SERVICE_DESIGN_DECISIONS.md](docs/SERVICE_DESIGN_DECISIONS.md) records
why the service is shaped the way it is. [CLAUDE.md](CLAUDE.md) collects the conventions and traps
that matter when changing the code rather than using it.

## Results

[docs/results/](docs/results/README.md) publishes the reports for **landmark runs** — the v1.35
baseline, a 12-run pair on two single-cluster installs (OpenShift and KinD), and the designed
AuthBridge plugin-overhead study run on the same image. All of them are generated from S3 artifacts,
never hand-edited. Earlier versions are archived under [docs/archive/](docs/archive/2026-10-04/README.md).

For the plugin question specifically, read [docs/PLUGIN_OVERHEAD.md](docs/PLUGIN_OVERHEAD.md) first:
it explains the sidecar/preset/judge vocabulary and carries the conclusions, the headline being that
on both clusters almost all of the cost is the IBAC judge's own LLM call — about 1.4–1.5 s per task —
while the sidecar and the other layers are below the noise floor.

## License

Apache 2.0 — see [LICENSE](LICENSE).
