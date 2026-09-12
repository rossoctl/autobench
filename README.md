# AutoBench

Automated benchmarking of agentic AI workloads on Rossoctl. AutoBench deploys a harnessed benchmark
workload, runs it, collects per-task telemetry, and publishes the results as a set of durable
artifacts.

It is a pure-Python HTTP service — no `kubectl`, no shelling out. Callers authenticate with their own
bearer token, which AutoBench uses for attribution and routing; it performs its own ROPC login to
Rossoctl over `httpx`.

## Components

| Component | What it is |
|---|---|
| `autobench-service` | The service. FastAPI, one per-issuer instance config, deployed in or beside a cluster. |
| `autobench-cli` | Stdlib-only client CLI that drives the service over its HTTP API end to end. |

## Benchmarks

**gsm8k** (single-turn arithmetic reasoning — the smoke test), **tau2** (multi-turn dialogue with a
server-side user simulator), and **appworld** (long-horizon app automation). See
[docs/BENCHMARKS_PRIMER.md](docs/BENCHMARKS_PRIMER.md).

## Getting started

[docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md) walks from deploy to result analysis with real,
copy-pasteable commands. [docs/SERVICE_DESIGN_DECISIONS.md](docs/SERVICE_DESIGN_DECISIONS.md) records
why the service is shaped the way it is. [CLAUDE.md](CLAUDE.md) collects the conventions and traps
that matter when changing the code rather than using it.

## Results

[docs/results/](docs/results/README.md) publishes the reports for **landmark runs** — the current
v1.27 cross-cluster baseline and the designed AuthBridge plugin-overhead study. All of them are
generated from S3 artifacts, never hand-edited.

For the plugin question specifically, read [docs/PLUGIN_OVERHEAD.md](docs/PLUGIN_OVERHEAD.md) first:
it explains the sidecar/preset/judge vocabulary and carries the conclusions, the headline being that
our two clusters disagree about *which* plugin layer costs anything — so no absolute per-task figure
is portable between them.

## License

Apache 2.0 — see [LICENSE](LICENSE).
