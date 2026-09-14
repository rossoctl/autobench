# Working in this repo

AutoBench is a FastAPI service that runs agent benchmarks (gsm8k, tau2, appworld) against agents
deployed on a Rossoctl-managed Kubernetes cluster, and publishes the results as artifacts to S3.
`docs/DEVELOPER_GUIDE.md` is the reference for using it; this file covers the things that are
non-obvious when *changing* it.

## Layout

| path | what it is |
|---|---|
| `src/autobench/` | the service. `app.py` wires routes, `routes/` is the HTTP surface, `runner/` executes a run, `benchmarks/registry.py` declares each benchmark. |
| `src/autobench/cli.py` | the `autobench-cli` entry point — a client, not a second implementation. |
| `tests/` | pytest, `asyncio_mode = "auto"`, `respx` for HTTP mocking. No cluster needed. |
| `reference/` | operator scripts and report generators. Not shipped in the package. |
| `deploy/` | Kubernetes manifests. These pin the image tag. |
| `docs/` | prose docs plus `docs/results/` — the curated landmark reports. |

## Commands

```sh
uv run pytest -q                      # 167 tests, ~40 s, no cluster required
uv run autobench-service              # local service on :8000
python3 reference/gen_toc.py <file>   # regenerate a doc's <!-- toc --> block after editing headings
```

Run `gen_toc.py` on any doc whose headings you touched — the TOC is generated between markers and
will otherwise silently drift out of sync with the body.

## The service shells out to nothing

Everything talks to Rossoctl over HTTP with `httpx`. There is no `subprocess`, no `kubectl`, no
`oc` — see `docs/SERVICE_DESIGN_DECISIONS.md` and `docs/KUBECTL_DEPENDENCY_INVENTORY.md`. If a task
seems to need a shell command, it needs an API call instead. Keep it that way: the service runs as a
pod with a service account, and a shell-out would need a binary and a kubeconfig that pod does not
have.

Config is per-instance and keyed by issuer (`iss`), in `instances/` — which is gitignored, because
those files hold ROPC service credentials.

## Traps that have cost real time

**A `benchmarks/registry.py` change needs an image rebuild before an e2e run means anything.** The
edit–pytest–commit–rerun loop silently validates the *old* baked-in value, because the cluster runs
the image, not your working tree. On KinD, `kind load docker-image` bypasses the registry, so a
pushed tag is not necessarily the tag the cluster is running.

Where the validation ladder ends depends on *what* you changed, because `GET /benchmarks` only
returns `name`, `mcp_image`, `agents` and `default_model` (see `_summary` in `routes/benchmarks.py`).
An MCP image or default model shows up there; an **agent `container_image`, `extra_env` entry or
`model_override` does not** and needs `GET /benchmarks/{name}`, which dumps the whole definition.
Confirming a registry change against the list endpoint alone can pass while the thing you edited is
still the old value.

**`InstanceConfig` takes Pydantic's default `extra="ignore"`** — unlike `DeployBenchmarkRequest`
next to it, which sets `extra="forbid"` deliberately. So an older image accepts a new instance field
and drops it *silently*. Adding one is never self-verifying: read the value back and confirm it took
effect.

**`GET /benchmarks` returns `items`, not `benchmarks`.** Small, but it has broken scripts twice.

**Every agent and MCP image is pinned to `:latest`, so an upstream fix arrives by re-pull with no
tag edit — and a tag tells you nothing about what is running.** The operator renders
`imagePullPolicy: Always` for these workloads (`registry.py` never sets `image_pull_policy`; that
default is the operator's, so verify it rather than assume it), which means a *newly created* pod
gets the new image. A pod that is already running does not, indefinitely. Leftover `team1`
deployments from a run two days earlier were still serving a six-week-old digest; the driver's
teardown-before-deploy clears them, but a hand-run experiment will quietly measure the old code.
Local tooling has the same failure: `podman run ...:latest` reuses a cached image silently — pass
`--pull=always` before concluding anything about upstream source. Compare **digests**, off
`.status.containerStatuses[].imageID` on the pod, never tags.

**`Model endpoint ... is unreachable` is usually not a network fault.** As of agent
`0.3.5.dev145` every task opens with a `GET /v1/models` health probe capped at a hard 10 s with no
retry, and any failure — including a slow TLS handshake — fails the whole task before the model is
called. On the VPN-routed KinD gateway this costs ~14% of tasks and roughly 60% on the sidecar legs,
while OpenShift never trips it. Before touching routing, check the endpoint by hand: a fast `401`
from inside the agent pod means the path is fine and you are looking at the probe. Details and the
suggested upstream fix are Bug 3 in `docs/exgentic-agent-bug-report-20260901.md`. There is no env
var for it, so a KinD matrix run against this agent version is not comparable to a pre-`dev145`
baseline on pass rate.

**Never bare-replace the strings `benchmarking` or `benchmarker`.** The S3 bucket
(`rossoctl-benchmarking`), the Keycloak user (`benchmarker`), and the `BM_*` env prefix deliberately
kept their old names through the rename to `autobench`; `benchmarker` also appears ~145 times where
only a handful are the container image. A global replace breaks the bucket and the auth path at
once.

**A Deployment's selector is immutable.** Changing one is create → verify → delete, not `apply`.
And a stale Deployment left running keeps serving its old `api_base`, which looks exactly like a
config bug in the new one.

## Long runs

A full 12-run takes 1–2 hours. Two rules, both learned the hard way:

- **Detach in-process.** The Bash tool kills the process *group*, background or not, so wrap the
  driver in a `os.setsid()` call before importing it (macOS has no `setsid(1)`).
- **One platform at a time, never in parallel.** Both clusters front the same LLM gateways, so
  concurrent runs contend and the latency numbers become meaningless.

If a poller dies, the server-side run keeps going — **adopt it and merge the results** rather than
re-running the leg.

## Results and reports

`results/` (repo root) is scratch and gitignored — that ignore is anchored as `/results/` on
purpose, because a bare `results/` also matches at any depth and was silently swallowing
`docs/results/`.

`docs/results/` is tracked and holds **landmark runs only**; see `docs/results/README.md` for what
qualifies. Everything there is generated by the `reference/gen-*.py` scripts and must never be
hand-edited — fix the generator and regenerate, or the report will disagree with the artifacts it
cites.

**This repo is public, and so is the S3 bucket** (readable *and listable* anonymously). Generated
reports print a platform label, never the Service endpoint. Keep it that way when touching a
generator's header block. Artifacts carry ids, counts and durations — never prompts or model
outputs — and `span_report.*` publishes a fixed whitelist of fields for that reason.

## Git

Push directly to `main` while there is a single committer; open a PR once that changes.
Sign off commits (DCO is enforced on the upstream repos this work feeds).
