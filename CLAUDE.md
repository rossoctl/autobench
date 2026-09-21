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
uv run pytest -q                      # 183 tests, ~40 s, no cluster required
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

How far a check can take you depends on *what* you changed, because `GET /benchmarks` only
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

**`Model endpoint ... is unreachable` means you are on `dev145` — fixed in `dev146`.** That agent
opened every task with a `GET /v1/models` probe capped at a hard 10 s with no retry, so any failure —
including a slow TLS handshake — failed the whole task before the model was called: 12 of 141 tasks on
KinD, 0 on OpenShift. `0.3.5.dev146+gff7ef6a37` fixed it (retry, per-process memo, caller's timeout
honoured, `EXGENTIC_SKIP_MODEL_PROBE`, per-cause error text) and the same legs now run clean —
verified, see Bug 3 and its Resolution section in `docs/exgentic-agent-bug-report-20260901.md`.

Two things survive the fix. **The dev145 matrices are still contaminated** and are not a baseline: a
killed task leaves a report row with zero tokens that skews every per-task stat, so pass rates from
that pair need the `total − probe_failures` denominator. And the message wording is the *old* one — if
you see `is unreachable` rather than `did not respond ... after N attempt(s): <cause>`, the pod is
running dev145, whatever `:latest` points at now. Check the digest on the pod, not the tag.

**Never bare-replace the strings `benchmarking` or `benchmarker`.** The S3 bucket
(`rossoctl-benchmarking`), the Keycloak user (`benchmarker`), and the `BM_*` env prefix deliberately
kept their old names through the rename to `autobench`; `benchmarker` also appears ~145 times where
only a handful are the container image. A global replace breaks the bucket and the auth path at
once.

**A Deployment's selector is immutable.** Changing one is create → verify → delete, not `apply`.
And a stale Deployment left running keeps serving its old `api_base`, which looks exactly like a
config bug in the new one.

## Long runs

A full 12-run takes 1–2 hours. Three rules, all learned the hard way:

- **Detach in-process.** The Bash tool kills the process *group*, background or not, so wrap the
  driver in a `os.setsid()` call before importing it (macOS has no `setsid(1)`).
- **One platform at a time, never in parallel.** Each platform has its *own* litellm deployment —
  hence its own completion cache and its own key table, which is why a key copied between them always
  401s — but the same upstream model providers sit behind both. Concurrent runs therefore contend
  where it matters and the latency numbers become meaningless.
- **Space the legs that share prompts, or the gateway pays for them.** litellm caches completions
  keyed on the request body, so a leg whose task set is a *prefix* of an earlier leg's replays that
  leg's answers instead of generating them. Task selection is deterministic and driven by
  `max_tasks`, so #1 ⊂ #2 ⊂ #3 and #5–#8 are all the same first five — and plugins do not break the
  collision, because they change what happens *around* the call, not the body. Measured TTL on the
  internal gateway is **~10 min** (survival curve: HIT at 3/5/7/9, MISS at 11/13/15/18/21), so
  `BM_CACHE_GAP=900` in `reference/run-12.py` clears it with margin. Only the shortfall is slept, so
  pair it with a `BM_ORDER` that interleaves the cache groups — that is the difference between ~120
  and ~42 minutes of sleeping. **The hit detector is the response `id`, never latency**: a replay can
  take as long as a miss.
- **The gap spaces legs, and one residual is inside a leg: tau2's opening call.** It is
  byte-identical for every tau2 task (5054 input tokens on all 60 v1.28 tasks, both platforms) —
  the prompt carries no task text, because the scenario lives in the MCP user simulator and the task
  id rides in the session metadata. Consecutive tasks are seconds apart, so no `BM_CACHE_GAP` value
  reaches it: one generation serves the leg. ~5–6% of input, ~2–3% of output, ~2–3% of chat latency,
  **tau2 only, and symmetric across platforms** — so it does not bias the cross-cluster comparison,
  but never read a tau2 first-call latency as a measurement. Nothing collides *within* a task: each
  call re-sends the grown history, and no task in the matrix repeats even an input-token count among
  its own calls. Side benefit: two slow legs flip their first-call output token count mid-leg at
  **+641 s and +632 s**, confirming the ~10 min TTL from an independent signal.

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

Attribute AI assistance with **`Assisted-By:`**, never `Co-Authored-By:` — GitHub reads the latter as
a structural trailer and adds a second *author* to the commit and to the repo's contributor stats.
So a message ends:

```
Assisted-By: Claude Opus 5 <noreply@anthropic.com>
Signed-off-by: Your Name <you@example.com>
```

The 46 commits that carried `Co-Authored-By` were rewritten on 2026-09-17 (`git filter-branch
--msg-filter`, trailer key substituted, everything else byte-identical: same trees, same author and
committer identities and dates) and force-pushed. **So SHAs before that rewrite are stale** — a
pre-rewrite hash quoted in an issue, a doc or a report resolves to nothing. If you ever need the old
history it is a local ref only, `refs/original/refs/heads/main`, on the machine that did it.
