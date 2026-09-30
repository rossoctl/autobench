# AutoBench Service — Admin Guide

**Last modified:** 2026-09-30T01:38:44Z

> Hand-maintained, unlike the generated `results/12run-*.md` files which stamp themselves. Bump the
> line above when you edit this guide.

How the AutoBench Service gets onto a cluster and stays trustworthy there: prerequisites, the
environment that differs between platforms, the Helm chart, and the verification that actually
proves the install. *Driving* the Service once it answers is a different document —
[`DEVELOPER_GUIDE.md`](./DEVELOPER_GUIDE.md).

- **Every host below is a placeholder on `example.com`** — this repo is public, so it names
  platforms rather than endpoints. Route *shapes* are real
  (`<service>-<namespace>.apps.<cluster>.example.com`), so substitute your own domain and the
  recipes work unchanged.
- **The cluster names are samples, and so is every endpoint they stand for.** `ykt2`/`ykt3`/`ykt5`
  are the OpenShift clusters this project happens to run on, reached from the Internet; the KinD
  cluster is a laptop on an organisation intranet. Nothing here requires any of them, or a
  particular LLM service (§3.5) — they are the worked examples, and the chart is deliberately
  generic.
- **No secret value appears in this guide, and none should appear in your terminal.** Every script
  here reads credentials from the environment or from a `chmod 600` file and reports them only as a
  truncated SHA-256. If you find yourself about to `echo` one, don't.
- Two install paths exist and both are supported: the **Helm chart** at `deploy/helm/autobench`
  (§5) and the **raw manifests** under `deploy/` (§8). They render the same objects, and that
  equivalence is enforced by a check, not asserted.

**In a hurry?** §2.4 is the one command that tells you whether the cluster is ready, §5 is the
install, §6 is the only verification that means anything.

<!-- Regenerate this list: python3 reference/gen_toc.py docs/ADMIN_GUIDE.md -->
<!-- toc -->

**Contents**

- [1. What you install, and what you don't](#1-what-you-install-and-what-you-dont)
  - [The two cluster shapes](#the-two-cluster-shapes)
- [2. Prerequisites](#2-prerequisites)
  - [2.1 Rossoctl v0.8.0 or later](#21-rossoctl-v080-or-later)
  - [2.2 Workstation tooling](#22-workstation-tooling)
  - [2.3 Cluster-side checklist](#23-cluster-side-checklist)
  - [2.4 The preflight script](#24-the-preflight-script)
- [3. Environment variables](#3-environment-variables)
  - [3.1 Install-time — your shell only, never a values file](#31-install-time--your-shell-only-never-a-values-file)
  - [3.2 The Service pod](#32-the-service-pod)
  - [3.3 The workload pods — injected, not configured](#33-the-workload-pods--injected-not-configured)
  - [3.4 KinD and OpenShift differ — and the differences fail silently](#34-kind-and-openshift-differ--and-the-differences-fail-silently)
  - [3.5 The LLM gateway: two named profiles](#35-the-llm-gateway-two-named-profiles)
  - [3.6 The `benchmarker` password: how it gets in, and how it is checked](#36-the-benchmarker-password-how-it-gets-in-and-how-it-is-checked)
- [4. The instance-config Secret](#4-the-instance-config-secret)
- [5. Installing with Helm](#5-installing-with-helm)
  - [5.1 OpenShift](#51-openshift)
  - [5.2 KinD](#52-kind)
  - [5.3 Adopting an install made from the raw manifests](#53-adopting-an-install-made-from-the-raw-manifests)
  - [5.4 Upgrade, rollback, uninstall](#54-upgrade-rollback-uninstall)
- [6. Verifying the install](#6-verifying-the-install)
- [7. Symptoms that lie](#7-symptoms-that-lie)
- [8. Appendix: the raw-manifest path, and two KinD-only objects](#8-appendix-the-raw-manifest-path-and-two-kind-only-objects)

<!-- /toc -->

## 1. What you install, and what you don't

The Service is a single stateless pod. It holds no benchmark data and no cluster credential: it
authenticates to Rossoctl per request with its own ROPC identity, and Rossoctl performs every
cluster operation server-side. Nothing here shells out to `kubectl` or `oc`.

| object | name | created by | notes |
|---|---|---|---|
| Deployment | `autobench-service` | chart / `deploy/deployment.yaml` | 1 replica, read-only root filesystem, arbitrary-UID safe |
| Service | `autobench-service` | chart / `deploy/service.yaml` | ClusterIP `:8080`, port named `http` |
| Route | `autobench` | chart (`platform: openshift`) | edge TLS; host generated as `autobench-<namespace>.apps.<cluster>` |
| HTTPRoute | `autobench` | chart (`platform: kind`) | attaches to the shared istio gateway |
| Secret | `autobench-instances` | **you, out-of-band** (§4) | ROPC credentials — never passes through Helm values |

Everything else the Service depends on belongs to the platform and must exist **before** the
install is useful:

- a Keycloak user (`benchmarker` by convention) with the **`rossoctl-operator` realm role**;
- `openai-secret` (`apikey`) and `hf-secret` (`hf-token`) in **every namespace you deploy
  benchmarks into** — `team1` unless you have changed the specs, since all 24 entries in
  `reference/run12_specs.json` name it;
- an OTEL collector that writes to an MLflow the Service can read (§3.4).

### The two cluster shapes

| shape | example | consequence |
|---|---|---|
| **single-cluster** — Service and agents together | KinD, ykt5 | agents reach the collector over service DNS; no extra ingress |
| **split** — Service here, agents there | ykt3 hosts the Service, agents run on ykt2 | the instance config carries `agent_endpoint_template`/`mcp_endpoint_template`, and the agents export telemetry back through an **edge Route** on `:443` whose target port resolves to the collector's 8335 |

The shape is not a chart value. It lives entirely in the instance config, so one chart installs
both.

## 2. Prerequisites

### 2.1 Rossoctl v0.8.0 or later

This is the hard one, because falling short of it does not error. An older operator accepts the
deploy request the Service sends and **silently drops fields it does not know** —
`k8sResourceLimits` among them — so the benchmark comes up in a shape nobody configured and every
subsequent measurement is quietly off.

Read the version off the **backend**. The operator subchart carries its own, lower, version line,
and comparing that one rejects a perfectly current cluster:

```bash
oc -n rossoctl-system get deploy rossoctl-backend \
  -o jsonpath='{.metadata.labels.app\.kubernetes\.io/version}{"\n"}'    # 0.8.0-rc.2
```

A release-candidate suffix counts: `0.8.0-rc.2` satisfies "≥ 0.8.0". Both
`reference/preflight.py` and `reference/ocp-service-bootstrap.sh` assert this before doing
anything else.

### 2.2 Workstation tooling

| tool | needed for | note |
|---|---|---|
| `kubectl` (or `oc`) | everything | `oc` only for `Route` conveniences; `kubectl` can do it all |
| `helm` ≥ 3.8 | §5 | 3.8 is the floor for the OCI/`--kube-context` behaviour used here |
| `jq`, `curl` | the bootstrap scripts | secrets move through `jq` via the *environment*, never argv |
| `python3` | `preflight.py`, `helm-parity-check.py`, `gen_toc.py` | `kind-collector-mlflow.py` also needs `pyyaml` |
| `kind` + a container engine | the KinD path only | the image is built locally and `kind load`ed |

### 2.3 Cluster-side checklist

Each row is a failure that has actually been shipped, and the right-hand column is why you cannot
find it by reading logs at the layer where it hurts.

| prerequisite | where | if missing |
|---|---|---|
| Rossoctl ≥ v0.8.0 | `rossoctl-system` | fields dropped from the deploy request, silently (§2.1) |
| `agentruntimes` + `agentcards` CRDs | cluster | `/deploy` fails at the operator |
| `benchmarker` user with a **password credential**, **ROPC enabled** on the client | realm | token request 400s; on this realm a missing `firstName`/`lastName` yields `Account is not fully set up`. Checked in two tiers, because ROPC alone cannot distinguish a wrong password from an absent one (§3.6) |
| `rossoctl-operator` realm role on that user | realm | `/deploy` surfaces the operator's 403 as a **502** |
| `openai-secret` / `apikey` | `team1` | empty or foreign key ⇒ a 401 **per completion, mid-run**; the leg finishes with zeroes rather than failing |
| `hf-secret` / `hf-token` (may be empty) | `team1` | MCP pod `CreateContainerConfigError`, the agent then crash-loops against it, and the *run* reports 424 |
| OTEL collector, HTTP receiver on **:8335** | `rossoctl-system` | an agent pointed at 4318 hard-fails into `CrashLoopBackOff` |
| an MLflow the collector **writes to** and the Service can **read** | varies (§3.4) | the run **passes** with `model: "unknown"` and every token count 0 |

**On the collector port: 8335 and only 8335.** Every cluster's `otel-collector-config` declares
receivers on 4317/4318, and reading that ConfigMap will tell you to use 4318. It is wrong — the
Deployment moves the HTTP receiver with a command-line override, so 4318 never listens:

```bash
oc -n rossoctl-system get deploy otel-collector \
  -o jsonpath='{.spec.template.spec.containers[0].command}'
# ["/otelcol-contrib","--config=/etc/otelcol-config/base.yaml",
#  "--set","receivers::otlp::protocols::http::endpoint=0.0.0.0:8335"]
```

The same principle applies to MLflow: read the target and the experiment id from the collector's
own exporter block rather than choosing them. `traces_endpoint` and the `x-mlflow-experiment-id`
header are what the *writer* uses, and an instance config that disagrees points the Service at an
experiment nothing writes to.

### 2.4 The preflight script

`reference/preflight.py` checks every row of §2.3 against the live cluster. It is **read-only** —
no writes, no deploys — and prints every credential as an 8-char SHA-256 prefix unconditionally,
with no attempt to decide which values are sensitive.

```bash
python3 reference/preflight.py --platform kind      --context kind-rossoctl
python3 reference/preflight.py --platform openshift --context <ykt5-ctx>
python3 reference/preflight.py --platform openshift --context <ykt3-ctx> \
                              --workload-context <ykt2-ctx>          # split shape
python3 reference/preflight.py --json                                # machine-readable
```

Exit status is 0 when nothing FAILed; warnings do not fail the run. Twelve sections, in the order
a request travels: tooling, cluster reachability, Rossoctl version and CRDs, namespaces, workload
secrets, the collector, MLflow, ingress, the instance config, identity, an audit of any existing
install, and the local chart.

```
Workload secrets (per team namespace)
  ok    team1/openai-secret apikey — sha8 0239f193
  ok    team1/hf-secret hf-token — empty value (fine)

OTEL collector (the write half of the telemetry chain)
  ok    collector HTTP receiver on :8335 — read from the Deployment's command
  ok    collector traces_endpoint — http://mlflow-reader.rossoctl-system.svc.cluster.local:5000/v1/traces
  ok    collector x-mlflow-experiment-id — 0

MLflow (the read half — this is what turns a run into a token report)
  ok    MLflow traces API answers 200 — experiment 0, 1 trace(s) visible
  ok    collector exports to mlflow-reader
...
47 ok, 0 warning(s), 0 failure(s) — ready to install
A clean preflight is necessary, not sufficient: only a 1-task leg with a non-zero token row proves the whole chain.
```

Three things about how it reads the cluster are worth knowing, because each was a false alarm
first:

- **Ports and targets come from live objects**, never from a manifest or a ConfigMap — the
  collector's port from the Deployment's `command`, MLflow's identity from the exporter.
- **`Route` has no CRD.** It is served by the aggregated openshift-apiserver, so
  `get crd routes.route.openshift.io` reports it missing on a cluster that plainly serves Routes.
  The script asks `api-resources --api-group=route.openshift.io` instead.
- **A split shape is not a broken one.** With `agent_endpoint_template` in the instance config and
  no `--workload-context`, the workload checks are *skipped* rather than run against the wrong
  cluster — and a `workload_otel` endpoint on `:443` is resolved through the Route to the
  collector's port instead of being rejected.

The **identity** section is the one that needs a credential from you, and it is not optional in
practice: every `/deploy` the Service makes is a ROPC login as `benchmarker`, so a password that is
absent, stale or blocked by a required action does not degrade the install — it makes every run fail
with a 502 wrapping a 403. Pass it in and preflight logs in for real:

```bash
python3 reference/preflight.py --platform kind --context kind-rossoctl \
        --password-file ~/.rossoctl-kind/benchmarker.pass
```

With no password at all the script falls back to the one inside the instance Secret, which is the
copy that actually matters. §3.6 covers the four intake routes, the two tiers of the check, and what
each failure cause means.

## 3. Environment variables

There are three populations, and conflating them is how a key ends up in the wrong cluster. They
are, in order: what you export **to run the installer**, what the **Service pod** reads, and what
the operator injects into the **workload pods**.

### 3.1 Install-time — your shell only, never a values file

Nothing in this table is a Helm value, a ConfigMap, or a committed file. Each is read by a script,
used once, and hashed if it is reported at all.

| variable | used by | notes |
|---|---|---|
| `KC_SERVICE_USERNAME` | both bootstrap scripts, `preflight.py` | the ROPC login the Service uses against Rossoctl (default `benchmarker`); `--username` overrides |
| `KC_SERVICE_PASSWORD` | both bootstrap scripts, `preflight.py` | **required**; written into the instance file, never echoed. `--password-file` / `--password-stdin` / `--password` take precedence (§3.6) |
| `KC_SERVICE_CLIENT_SECRET` | both bootstrap scripts | only if the Keycloak client is confidential |
| `KC_USER_PASSWORD` | `kind-post-setup.sh` | the password to *seed*; the same three flags override it, and it falls back to `~/.rossoctl-kind/benchmarker.pass` (`KC_CRED_FILE`) |
| `KC_ADMIN_PASSWORD` | `kind-post-setup.sh`, `preflight.py` | optional — read from the in-cluster `keycloak-initial-admin` Secret when unset. In `preflight.py` it enables the Admin-API tier of §3.6 |
| `KC_ADMIN_USER` | `preflight.py` | optional, default `admin` — the master-realm admin the Admin-API tier logs in as |
| `KC_ISS` | `preflight.py` | optional — the issuer to log in against, when neither `--iss` nor the instance file supplies one |
| `LLM_PROFILE` | both bootstrap scripts, `kind-post-setup.sh`, `preflight.py` | `intranet` \| `internet` — selects one of the two gateway variable sets (§3.5) |
| `INTRANET_LLM_*` / `INTERNET_LLM_*` | `reference/llm-profiles.sh`, read by all of the above | the profiles themselves: base, model, key file, optional bypass list (§3.5) |
| `BM_WORKLOAD_LLM_KEY` | `kind-post-setup.sh` | the workload LLM key; falls back to the selected profile's key file, else `~/.rossoctl-kind/litellm.key` (`LLM_KEY_FILE`) |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | `ocp-service-bootstrap.sh` | override `--s3-from`, which otherwise copies them in-process from an existing instance file |
| `WORKLOAD_LLM_API_BASE`, `WORKLOAD_LLM_MODEL` | both bootstrap scripts | set by `LLM_PROFILE` when you use one; an explicit export wins. The gateway has **no default** on purpose: this repo is public |
| `MLFLOW_URL`, `MLFLOW_EXPERIMENT_ID`, `MLFLOW_WORKSPACE`, `MLFLOW_TOKEN_SECRET` | `ocp-service-bootstrap.sh` | the id and workspace default to what the collector exports |
| `WORKLOAD_OTEL_ENDPOINT`, `WORKLOAD_OTEL_INSECURE`, `WORKLOAD_AGENT_RUNNER` | both bootstrap scripts | see §3.4 on the runner |
| `IMAGE`, `CLUSTER`, `KUBE_CONTEXT`, `REALM`, `CLIENT`, `KC_HOST` | `kind-post-setup.sh` | plain overrides, no secrets |

Two conventions here are deliberate and both cost real time when broken:

**The LLM key is `BM_WORKLOAD_LLM_KEY`, not `OPENAI_API_KEY`.** That name is commonly exported in
a developer's shell profile for an unrelated provider, and the script writes whatever it finds into
cluster Secrets. A namespaced name cannot be inherited by accident. (Inside the pod the value
still arrives as `OPENAI_API_KEY` — the registry maps it from the Secret's `apikey`.)

**Credential files are refused unless they are `600` or `400`,** and they are meant to outlive the
cluster: neither the `benchmarker` password nor the LLM key changes across a rebuild.

```bash
umask 077; mkdir -p ~/.rossoctl-kind
printf '%s' '<benchmarker password>' > ~/.rossoctl-kind/benchmarker.pass
printf '%s' '<llm key>'              > ~/.rossoctl-kind/litellm.key
```

### 3.2 The Service pod

The container reads exactly two environment variables from the Deployment, and both are set by the
chart:

| variable | value | why |
|---|---|---|
| `SERVICE_INSTANCES_DIR` | `/etc/service/instances` | where the `autobench-instances` Secret is mounted read-only |
| `SERVICE_PORT` | `8080` | read by the entrypoint (`uvicorn`), not by the settings model |

Everything else is a `SERVICE_`-prefixed setting with a working default, overridable through
`extraEnv` in the values file. These are the ones worth knowing:

| setting | default | when you would change it |
|---|---|---|
| `SERVICE_LOG_LEVEL` | `INFO` | `DEBUG` while diagnosing an auth or deploy failure |
| `SERVICE_HTTP_TIMEOUT_SECONDS` | `30` | a slow Rossoctl API |
| `SERVICE_JWKS_CACHE_SECONDS` | `300` | rarely |
| `SERVICE_EXPORT_SETTLE_MAX_SECONDS` | `20` | a fast run can be exported *before* its child spans land, yielding `model: "unknown"` and zero tokens; the Service re-reads until the record set stops growing, and this is that budget |
| `SERVICE_EXPORT_SETTLE_INTERVAL_SECONDS` | `2` | with the above |
| `SERVICE_SPAN_REPORT_MAX_ROWS` | `200000` | only to cap a pathological trace — the export is built in memory |

```yaml
# values fragment
extraEnv:
  - name: SERVICE_LOG_LEVEL
    value: DEBUG
```

The settings model uses Pydantic's `extra="ignore"`, so a **misspelled `SERVICE_*` name is
accepted and discarded without a word**. If a setting seems not to take effect, suspect the
spelling before the code.

### 3.3 The workload pods — injected, not configured

You do not set these. The Service derives them per deploy from the benchmark definition
(`src/autobench/benchmarks/registry.py`) and the instance config, which is why an agent's
environment cannot drift from the run that measured it. They are listed so you can recognise them
in a pod spec:

| variable | source | note |
|---|---|---|
| `OPENAI_API_KEY` | `secretKeyRef` → `openai-secret` / `apikey` | never a literal value in the pod spec |
| `HF_TOKEN` | `secretKeyRef` → `hf-secret` / `hf-token` | gsm8k only; presence is the requirement, not content |
| `OPENAI_API_BASE`, `LLM_API_BASE` | instance `workload_llm.api_base` | any inherited values are **dropped** and re-injected |
| `EXGENTIC_DEFAULT_RUNNER` | instance `workload_agent_runner` | §3.4 |
| `EXGENTIC_OTEL_ENABLED` | `true` when `workload_otel.enabled` | |
| `OTEL_EXPORTER_OTLP_ENDPOINT` / `_PROTOCOL` / `_INSECURE` | instance `workload_otel` | the endpoint is the `:8335` one |

### 3.4 KinD and OpenShift differ — and the differences fail silently

The Service's own objects are nearly identical across platforms. What differs is everything it
points *at*.

| | KinD | OpenShift | if you get it wrong |
|---|---|---|---|
| LLM gateway | **not a platform difference** — either platform may use a service on the intranet or on the Internet, subject to the asymmetric rule in §3.5 | ditto | no two services share a key table, so a key moved across does not fail closed — it 401s per completion, mid-run |
| `openai-secret` | a key issued by **that cluster's own** service | ditto | same |
| MLflow read path | `mlflow-reader` (§8), no auth | the cluster's own MLflow on `:8443` with a ServiceAccount bearer and `insecure_tls` | a refused read is invisible: the run passes, every token count is 0 |
| MLflow experiment | `0` | `1`, workspace `team1` on ykt5 | identical signature to the above |
| Keycloak dial | the **backchannel** service DNS — `iss` is unreachable in-cluster, since `*.localtest.me` resolves to pod loopback and there is no CoreDNS rewrite | the `iss` Route itself | JWKS and ROPC both fail at startup |
| collector endpoint | `http://otel-collector.rossoctl-system.svc.cluster.local:8335` | the same on a single-cluster install; an edge Route on `:443` when the agents live elsewhere | unreachable collector ⇒ agent `CrashLoopBackOff`, surfacing as a 424 on the run |
| pod security context | UID/GID/fsGroup pinned to 10001/0/10001 | `runAsNonRoot` + seccomp only | pinning 10001 can be **rejected** when it falls outside the project's allocated UID range |
| ingress | `HTTPRoute` on the shared gateway | `Route`, edge TLS | — |

**`workload_agent_runner` deserves its own warning.** The correct value tracks an agent image
pinned to `:latest`, and it has flipped between `direct` and `service` across rebuilds of that tag.
With the current image, `service` is what emits agent spans; `direct` yields a clean run with
**zero-token rows**, which is exactly what a broken MLflow looks like. Do not infer it — detect it,
by looking for a non-zero token row in `report.ndjson` (§6).

### 3.5 The LLM gateway: two named profiles

The gateway is the one piece of configuration that is neither discoverable from the cluster nor
shared between clusters, so it gets its own section — and it is configured as a **named profile**
rather than as a set of per-cluster values, because the failure it causes is silent.

Any OpenAI-compatible **LLM or LiteLLM service** can serve a deployment; nothing in AutoBench
requires a particular one, and the endpoints this project happens to use are examples, not part of
the product. Your organisation's will be different services with different hosts, model catalogues
and keys.

What decides which one a deployment may be pointed at is **the network the service sits on relative
to the cluster**, not which Kubernetes the cluster runs. Both platforms occur in both places: an
OpenShift cluster is often deployed on the organisation's intranet, and a KinD cluster runs on an
Internet server as readily as on a laptop on the VPN. `platform` and `llmProfile` are therefore
independent, and nothing derives one from the other.

The rule is **asymmetric**:

- An **Internet** cluster — KinD or OpenShift — must be configured with the access credentials for
  an LLM or LiteLLM service **on the Internet**. An intranet service is not routable from it.
- An **intranet** cluster — KinD or OpenShift — can be configured with the access credentials for an
  LLM or LiteLLM service **on the intranet or on the Internet**, whichever it is permitted to reach.
  So an intranet cluster has a genuine choice, and the profile records which way it went.

| | `intranet` | `internet` |
|---|---|---|
| what it points at | an LLM or LiteLLM service on the organisation's **intranet** | one on the **Internet** |
| which clusters may declare it | intranet clusters only — KinD or OpenShift | any cluster, on either network |
| why not the other | an Internet cluster cannot route to an intranet service — connect times out | an intranet cluster *may* use this; it is only barred where egress to the Internet is |
| model catalogue | ids from *that* service | **not** the same ids |
| key table | its own | its own — this is the whole problem |

#### The two variable sets

Each profile is one set of environment variables, distinguished only by prefix, defined in
`reference/llm-profiles.sh`:

| `intranet` | `internet` | what it is |
|---|---|---|
| `INTRANET_LLM_API_BASE` | `INTERNET_LLM_API_BASE` | the gateway origin — scheme + host, no path |
| `INTRANET_LLM_MODEL` | `INTERNET_LLM_MODEL` | a model id from **that** gateway's catalogue |
| `INTRANET_LLM_KEY_FILE` | `INTERNET_LLM_KEY_FILE` | `chmod 600` file holding that gateway's key (default `~/.rossoctl-llm/<profile>.key`) |
| `INTRANET_LLM_NO_PROXY` | `INTERNET_LLM_NO_PROXY` | optional explicit egress-proxy bypass list; otherwise built for you |

`LLM_PROFILE=intranet|internet` (or `--llm-profile`) selects one, and the scripts copy it into the
canonical `WORKLOAD_LLM_API_BASE` / `WORKLOAD_LLM_MODEL` / `LLM_KEY_FILE` they already read. An
explicitly exported `WORKLOAD_LLM_*` still wins, so nothing that worked before behaves differently.

Bases and model ids are not secrets, so keep them in a file instead of re-exporting per shell. The
**keys stay out of it**, in their own per-profile files:

```bash
mkdir -p ~/.rossoctl-llm
cat > ~/.rossoctl-llm/profiles.env <<'EOF'
INTRANET_LLM_API_BASE=https://<intranet LLM service host>
INTRANET_LLM_MODEL=openai/aws/claude-haiku-4-5
INTERNET_LLM_API_BASE=https://<Internet LLM service host>
INTERNET_LLM_MODEL=openai/Azure/gpt-5-mini-2025-08-07
EOF
umask 077
printf '%s' '<intranet service key>' > ~/.rossoctl-llm/intranet.key
printf '%s' '<Internet service key>' > ~/.rossoctl-llm/internet.key
```

Per-profile key files are the point: no two services share a key table, so one file holding "the"
key is exactly how a key gets used against the service that never issued it.

#### Selecting a profile for a deployment

The profile is declared **once**, as `llmProfile` in the chart values file, and everything else reads
it from there:

```yaml
# deploy/helm/values-kind.yaml            # deploy/helm/values-openshift.yaml
platform: kind                            # platform: openshift
llmProfile: intranet                      # llmProfile: internet
```

```bash
reference/ocp-service-bootstrap.sh  --llm-profile internet --cluster ykt5 --context <ctx> ...
LLM_PROFILE=intranet reference/kind-post-setup.sh
python3 reference/preflight.py --values deploy/helm/values-kind.yaml --context kind-rossoctl
```

The chart **does not** render the gateway into the Service pod, and cannot: the Service reads the
gateway from `workload_llm.api_base` in the instance-config Secret (§4) — not from its own
environment — and injects `OPENAI_API_BASE`/`LLM_API_BASE` onto each workload pod at deploy time
(§3.3). So `llmProfile` is a **declaration**, and it does two things that matter:

- the bootstrap scripts take the profile from it, so the instance file they write cannot disagree
  with the release installed beside it;
- `preflight.py` **FAILS** when the live instance file's `api_base` is not that profile's base:

```
FAIL  keycloak.localtest.me_8080.json: LLM gateway matches the internet profile — instance points
      at <internal host>, the internet profile declares <external host> — separate key tables, so
      this 401s per completion mid-run rather than failing at deploy
```

That is the entire point of naming the profiles: it converts a mid-run 401 storm, whose only visible
symptom is a leg that finishes with zeroes, into a pre-install error. `helm` rejects a
mistyped profile for the same reason — `llmProfile: intranett` fails the render rather than being
quietly ignored downstream.

#### Four details that have cost time

- **The key never travels in the instance file.** It lives only in `openai-secret` / `apikey` in each
  workload namespace, and reaches the pod as `OPENAI_API_KEY` through a `secretKeyRef`.
- **The proxy-bypass list is part of the gateway's configuration, not a separate concern.** Where the
  cluster injects an egress proxy, an in-network gateway base must be excluded from it or every
  completion leaves through a proxy that cannot reach it. `workload_llm.no_proxy` (and
  `disable_proxy: true`, which injects an empty `HTTP_PROXY`/`http_proxy`) are rendered onto the
  **agent** pod only. Both bootstrap scripts now build the list — loopback, the gateway host, the
  collector, and Keycloak's in-cluster service — where before only the OpenShift one did, so a KinD
  bootstrap without `--copy-from` silently lost it.
- **`WORKLOAD_LLM_BASE` was never a second setting.** It is an accepted alias for
  `WORKLOAD_LLM_API_BASE` in the KinD script, kept only so existing invocations keep working; the
  canonical name matches the instance-file field (`workload_llm.api_base`) on both platforms.
- **Do not verify a gateway with `curl`.** LiteLLM strips the `openai/` prefix, so the configured
  model name 403s by hand, and reasoning models reject `max_tokens` with a 400
  (`max_completion_tokens` is the accepted field). The only trustworthy probe is a 1-task `gsm8k`
  leg (§6).

### 3.6 The `benchmarker` password: how it gets in, and how it is checked

This credential is load-bearing **per run**, not per install. The Service holds no cluster
credential of its own: each `POST /benchmarks/{name}/deploy` is a fresh ROPC password grant as
`benchmarker` against the realm, and the resulting token is what Rossoctl authorises. An install can
therefore be completely healthy — pod Running, `/healthz` 200, `GET /benchmarks` listing every
benchmark — and still fail every single run with a 502 that wraps Keycloak's 403.

There is no Keycloak object scoped to `team1`, incidentally: `team1` is a Kubernetes namespace, and
the authorisation artifact for deploying into it is the **`rossoctl-operator` realm role** on the
user in the `rossoctl` realm. So "is the password set for team1" is really five questions — does the
user exist, is it enabled, does it have a `password` credential, is its profile complete enough for
the realm to issue a token, and does it carry that role — and the checks below answer them
separately.

#### Four ways in, in precedence order

Every script that needs the password takes the same four, most explicit first:

| route | used as | notes |
|---|---|---|
| `--password-file F` | `preflight.py`, both bootstrap scripts, `kind-post-setup.sh` | **preferred.** The value never reaches argv or an environment. The file must be `600` or `400` or it is refused |
| `--password-stdin` | same | for a pipeline or a secret manager: `pass show … \| script --password-stdin` |
| `--password P` | same | accepted, and warns every time: argv is world-readable through `ps` and `/proc/<pid>/cmdline`, and it lands in your shell history |
| the environment | `KC_SERVICE_PASSWORD` (bootstrap, preflight), `KC_USER_PASSWORD` (`kind-post-setup.sh`) | the original route, unchanged |

Two conveniences on top: `kind-post-setup.sh` falls back to `~/.rossoctl-kind/benchmarker.pass`
(`KC_CRED_FILE`), so on KinD no credential flag is ever needed; and `preflight.py` falls back to the
`service_credential` inside the live `autobench-instances` Secret, so it can check an install whose
password you do not have to hand.

Only the **source** is printed, alongside an 8-char SHA-256 prefix of the value:

```
Identity — the benchmarker credential every /deploy authenticates with
  ok    benchmarker password from file ~/.rossoctl-kind/benchmarker.pass — user benchmarker, sha8 8bb6f116
```

That hash is also how the supplied password is compared against the Secret's copy without either
being shown. **A mismatch is a FAILURE, not a warning** — the Secret's copy is the one the Service
presents, so a green preflight next to a Service that 502s on every deploy would be worse than no
check at all.

Because a trailing newline is welded onto the value by every editor, the file readers take the
**first line only**. A password with `\n` on the end fails ROPC with exactly the message a wrong
password gives.

#### Two tiers, because ROPC cannot tell three failures apart

Keycloak answers `invalid_grant` / "Invalid user credentials" identically for a **wrong password**, a
user with **no password credential**, and **no such user**. Those need three different fixes, so
`preflight.py` asks the Admin REST API first when it can, and the ROPC login second:

```
  ok    user benchmarker exists in realm rossoctl — id e7557a3c-0a9f-4427-9d1a-3ab69727abc4
  ok    password credential set on benchmarker — credential type(s): password
  ok    ROPC login as benchmarker — against http://keycloak.localtest.me:8080/realms/rossoctl
  ok    realm role rossoctl-operator
```

The admin tier needs `KC_ADMIN_PASSWORD`, or an in-cluster `keycloak-initial-admin` Secret (which is
how KinD ships — nothing to supply there). Without one it is skipped with a note and the ROPC login
stands alone as the behavioural check. When the tier *has* run, its finding is threaded into the
failure text, which is the difference between a guess and an answer:

```
FAIL  ROPC login as benchmarker — 'Invalid user credentials' — a password credential IS set on this
      user (checked above), so the password given to this script is not the one Keycloak holds.
```

The causes and their fixes:

| cause | what it means | fix |
|---|---|---|
| `Invalid user credentials`, password credential **is** set | the password you supplied is not the one Keycloak holds | supply the right one, or re-seed with `reference/keycloak-ensure-user.sh` |
| `Invalid user credentials`, **no** password credential | the user exists with no password at all | `reference/keycloak-ensure-user.sh` |
| `Invalid user credentials`, **no such user** | not in this realm | `reference/keycloak-ensure-user.sh`; check you have the right realm |
| `Account is not fully set up` | the password may be **correct** — a required action is pending, or `firstName`/`lastName` are unset, which this realm's user profile demands before it issues a token | set both (`kind-post-setup.sh` patches them), clear the required action |
| `unauthorized_client` / `invalid_client` | the client is confidential, or has `directAccessGrantsEnabled: false` | `KC_SERVICE_CLIENT_SECRET`, or `keycloak-ensure-user.sh`, which enables Direct Access Grants idempotently |
| login ok, role missing | every `/deploy` will 403 | grant the `rossoctl-operator` realm role |

#### Where in the flow each script checks

- **`kind-post-setup.sh`** seeds the user, then patches `firstName`/`lastName`, then grants the role.
  It deliberately calls `keycloak-ensure-user.sh` *without* `--verify`: before that patch the realm
  refuses a token no matter how correct the password is, so verifying there would fail on a healthy
  setup.
- **`kind-service-bootstrap.sh`** verifies at the end of that chain, immediately before it writes the
  password into the instance file, and **dies** rather than writing an unusable one. It also warns
  when the token carries no `rossoctl-operator` role. `SKIP_CRED_CHECK=1` writes the file anyway —
  only useful when Keycloak is not reachable from where you are running.
- **`ocp-service-bootstrap.sh`** reports the source as a precheck row and logs in as another,
  alongside the Rossoctl-version and gateway-profile checks.
- **`preflight.py`** is the read-only pre-`helm` gate, and the only one with the admin tier.

## 4. The instance-config Secret

One JSON file per issuer, keyed by `iss`, mounted read-only at `/etc/service/instances`. **No
install path creates it**: it carries the ROPC service credential and the S3 keys, so it is
generated out-of-band and must never pass through Helm values or a values file.

One script per platform, so the file has reproducible provenance instead of being hand-assembled:

```bash
# OpenShift — prechecks the whole chain (ten rows on a single-namespace cluster) before writing
reference/ocp-service-bootstrap.sh --cluster ykt5 --context <ctx> \
  --apps-domain apps.ykt5.example.com \
  --password-file ~/.rossoctl-ykt5/benchmarker.pass \
  --llm-api-base https://<external-gateway>/v1 \
  --out-dir instances/

# KinD — verifies the credential before writing it into the file
reference/kind-service-bootstrap.sh --context kind-rossoctl \
  --password-file ~/.rossoctl-kind/benchmarker.pass \
  --copy-from instances/keycloak.localtest.me_8080.json \
  --out-dir instances/
```

Both also accept `--password-stdin`, `--password`, or `KC_SERVICE_PASSWORD` in the environment, and
default the user to `benchmarker` (`--username` overrides) — see §3.6.

Then, on either platform:

```bash
kubectl -n rossoctl-system create secret generic autobench-instances \
  --from-file="<encoded-iss-host>.json=instances/<encoded-iss-host>.json" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n rossoctl-system rollout restart deploy/autobench-service
```

The filename encodes the `iss` **host** with `:` rewritten to `_`; the exact `iss` inside the file
is the source of truth. The Service reads instance files at startup, hence the restart. Note that
`PUT /config` is in-memory only — a correction that must survive a restart goes in the Secret.

Four properties of these scripts are load-bearing:

- **Values reach `jq` through the environment, not `--arg`.** argv is world-readable (`ps`,
  `/proc/<pid>/cmdline`); a process's environment is not.
- **The file is created `600` by `umask`,** not `chmod`ed afterwards, so there is no window in
  which credentials sit world-readable on disk.
- **`--copy-from` / `--s3-from` carry `s3` and `workload_llm` over** from an existing file. Those
  two are environment facts a script cannot discover, and a cluster rebuild should reproduce them
  rather than lose them — losing them is silent, and costs a run's artifacts.
- **A no-auth MLflow still needs a `bearer_token`,** which is not a contradiction: the Service
  mints a token *before* it reads, and with neither a bearer nor client-credentials the token
  helper raises, the route catches it, and the run fails soft into an empty token report. For the
  KinD reader the scripts emit an ignored placeholder.

## 5. Installing with Helm

```
deploy/helm/autobench/
  Chart.yaml            version = chart version; appVersion = the default image tag
  values.yaml           the two platform shapes, documented inline
  templates/            deployment, service, route, httproute, _helpers.tpl, NOTES.txt
deploy/helm/values-openshift.yaml   the OpenShift shape's overrides
deploy/helm/values-kind.yaml        KinD's overrides
```

`platform` selects the shape and is the only value most installs need to think about. It drives
the pod security context and which ingress object is rendered (§3.4). The chart **references but
never creates** `autobench-instances`.

**Parity with the raw manifests is the chart's correctness gate, and it has teeth.** Run this after
any template change:

```bash
helm lint deploy/helm/autobench
python3 reference/helm-parity-check.py     # 6 checks; "Chart and manifests agree."
```

It renders both platform shapes and diffs them against
`deploy/{deployment,service,kind/httproute}.yaml` and against the OpenShift patched render. The
standard `app.kubernetes.io/*` labels are deliberately **absent**: the sole label and selector is
`app: autobench-service`, because that is what the live Deployments select on, and a Deployment's
selector is immutable. If the check fails, the chart is wrong — not the manifests.

### 5.1 OpenShift

```bash
python3 reference/preflight.py --platform openshift --context <ctx> \
        --password-file ~/.rossoctl-ykt5/benchmarker.pass                  # 0 failures first
helm upgrade --install autobench deploy/helm/autobench \
  -n rossoctl-system --kube-context <ctx> -f deploy/helm/values-openshift.yaml
oc -n rossoctl-system rollout status deploy/autobench-service
HOST=$(oc -n rossoctl-system get route autobench -o jsonpath='{.spec.host}')
curl -fsS "https://$HOST/healthz"                                          # {"status":"ok"}
```

Leave `route.host` empty unless you need a specific name — the router generates
`autobench-<namespace>.apps.<cluster>`, which is the shape every recipe in the developer guide
assumes.

### 5.2 KinD

DEV/TEST only. On a **freshly rebuilt** cluster do not start here: `reference/kind-post-setup.sh`
builds and `kind load`s the image, re-seeds the `benchmarker` user, ensures the team secrets,
generates the instance config and creates the Secret. The chart is the last step of that, and
alone it installs a pod with nothing to authenticate as.

```bash
IMAGE=ghcr.io/rossoctl/autobench:v1.29 reference/kind-post-setup.sh    # first time / after a rebuild

python3 reference/preflight.py --platform kind --context kind-rossoctl \
        --password-file ~/.rossoctl-kind/benchmarker.pass
helm upgrade --install autobench deploy/helm/autobench \
  -n rossoctl-system --kube-context kind-rossoctl -f deploy/helm/values-kind.yaml
curl -fsS http://autobench.localtest.me:8080/healthz                  # {"status":"ok"}
```

`8080` there is the **host** port KinD publishes the istio gateway on; it is not this Service's
port, and the two matching is a coincidence. Note also that `kind load docker-image` bypasses the
registry, so a KinD pod's digest will not match the published one even at the same tag — compare
source, not digests, there.

### 5.3 Adopting an install made from the raw manifests

A chart cannot replace an existing Deployment — the selector is immutable — but it can take
ownership of one. Both clusters that predate the chart were adopted this way rather than
reinstalled, which is why neither had an outage: **the running pod is not recreated.**

```bash
for obj in deploy/autobench-service svc/autobench-service httproute/autobench; do   # or route/autobench
  kubectl -n rossoctl-system annotate "$obj" \
    meta.helm.sh/release-name=autobench meta.helm.sh/release-namespace=rossoctl-system --overwrite
  kubectl -n rossoctl-system label "$obj" app.kubernetes.io/managed-by=Helm --overwrite
done
helm upgrade --install autobench deploy/helm/autobench -n rossoctl-system -f <values>
```

Without the annotations Helm refuses the install with "invalid ownership metadata". Afterwards,
confirm the pod's `restartCount` and `creationTimestamp` are unchanged — adoption should be a
metadata-only operation.

**Read release ownership from the Secrets, not from `helm list`.** A `helm list --kube-context`
comparison across two clusters has returned identical output for different clusters here; the
release objects cannot lie about where they live:

```bash
kubectl -n rossoctl-system get secret -l owner=helm,name=autobench --context <ctx>
# sh.helm.release.v1.autobench.v1
```

### 5.4 Upgrade, rollback, uninstall

```bash
helm upgrade autobench deploy/helm/autobench -n rossoctl-system -f <values> \
  --set image.tag=v1.30
helm history  autobench -n rossoctl-system
helm rollback autobench 1 -n rossoctl-system
helm uninstall autobench -n rossoctl-system          # leaves autobench-instances behind
```

`helm uninstall` deletes only what the chart owns, so the instance Secret survives — which is what
you want, since regenerating it means re-reading credentials. The image tag is immutable, so
`imagePullPolicy: IfNotPresent` is correct and an upgrade means changing `image.tag`, never
restarting to pick up a rebuild.

## 6. Verifying the install

`/healthz` proves the pod runs. It proves nothing about the chain the pod exists to drive, and
every link in that chain has a failure mode that still returns 200. Work outward:

```bash
TOKEN=…   # ROPC; see DEVELOPER_GUIDE.md §3

curl -fsS "$BASE/healthz"
curl -fsS -H "Authorization: Bearer $TOKEN" "$BASE/benchmarks" | jq '.items[].name'
curl -fsS -H "Authorization: Bearer $TOKEN" "$BASE/benchmarks/gsm8k" | jq '.agents[0].container_image'
```

`GET /benchmarks` returns **`items`**, not `benchmarks` — that has broken scripts twice — and it
only summarises each benchmark (`name`, `mcp_image`, `agents`, `default_model`). An agent's
`container_image`, `extra_env` or `model_override` is visible only through
`GET /benchmarks/<name>`, so confirming a catalog change against the list endpoint alone can pass
while the thing you changed is still the old value.

Then the only check that means anything: **a 1-task gsm8k leg whose report carries a non-zero
token row.**

```bash
uv run autobench-cli --base "$BASE" run gsm8k --max-tasks 1     # see DEVELOPER_GUIDE.md §7.1
```

| what you look at | expected | what it proves |
|---|---|---|
| run status | `completed`, pass rate 1.0 | Service → Rossoctl → operator → agent → MCP → evaluator |
| tokens on the task row | non-zero (~320 in / 87 out) | agent → collector → MLflow → Service, the whole telemetry chain |
| `model` on the row | the gateway's model id, not `"unknown"` | the Service read the experiment the collector wrote to |
| pod `imageID` digest | matches the tag you installed (OpenShift) | the pod is not serving a stale `:latest` |

A zero-token row is the single most informative failure in this system, because it is the shared
signature of four unrelated causes: a refused MLflow read, an experiment-id mismatch, a collector
that cannot be reached, and `workload_agent_runner` set to the value the current agent image does
not emit spans for. Preflight separates the first three; only a run separates the fourth.

## 7. Symptoms that lie

| symptom | actual cause | how to confirm |
|---|---|---|
| run passes, every token count 0, `model: "unknown"` | refused MLflow read, wrong experiment id, unreachable collector, or the wrong `workload_agent_runner` | §2.4, then §6 |
| `/deploy` returns **502** | Keycloak or the operator returned 403 — the service credential no longer logs in, or the user lacks the `rossoctl-operator` realm role. The install itself looks healthy: the password is only used per deploy | `preflight.py --password-file …` (§3.6); then the role mapping in the realm |
| `/deploy` returns **424**, agent `CrashLoopBackOff` | collector endpoint on 4318, or `hf-secret` missing so the MCP pod never started | the collector's `command`; `get secret hf-secret` |
| token request 400 `Account is not fully set up` | the realm requires `firstName`/`lastName` for ROPC | the user's profile |
| tasks error on the first completion, run finishes with zeroes | `openai-secret` empty, or holding the *other* gateway's key | `preflight.py` prints both namespaces' `sha8`; compare them |
| `Model endpoint … is unreachable` on every task | the agent pod is running **dev145**, whatever `:latest` points at | the wording: dev146 says `did not respond … after N attempt(s)`. Compare the pod's `imageID` digest, never the tag |
| a config change appears to do nothing | `InstanceConfig` and the `SERVICE_*` settings both use `extra="ignore"` — an unknown field is dropped **silently** | read the value back; adding one is never self-verifying |
| the new `api_base` is ignored | a stale Deployment from an earlier bring-up is still serving the old one | list Deployments in the team namespaces by age |
| a pod serves old code at the right tag | `:latest` agent/MCP images: a *running* pod never re-pulls | tear down before deploying; compare `.status.containerStatuses[].imageID` |
| `mlflow-reader` restarts ~30 s after boot with no error in the log | OOMKilled — one MLflow 3.x worker idles at ~2.3 GiB, so a 2Gi limit is below the floor | the container's `lastState`, not its log |
| `get crd routes.route.openshift.io` says Routes are missing | Routes come from the aggregated apiserver and have no CRD | `kubectl api-resources --api-group=route.openshift.io` |
| `/healthz` 503, deploy 500 `httpx.ReadError`, agent CrashLoop — all at once, on KinD | a ztunnel restart un-enrolled older pods from the ambient mesh | recycle every pod older than ztunnel's Ready condition, in **all** namespaces |

## 8. Appendix: the raw-manifest path, and two KinD-only objects

The manifests under `deploy/` remain supported and are what the chart is checked against:

```bash
kubectl apply -f deploy/service.yaml -f deploy/deployment.yaml
oc patch deploy autobench-service --patch-file deploy/openshift/deployment-patch.yaml   # OpenShift
kubectl apply -f deploy/kind/httproute.yaml                                             # KinD
```

On OpenShift the patch must go on **before** the rollout, not after: it is what removes the pinned
UID that restricted-v2 would otherwise reject.

Two objects live under `deploy/kind/` and are in neither the chart nor the OpenShift path, because
neither has an OpenShift counterpart.

**`mlflow-reader.yaml`** — stock MLflow, no auth, serving the *same* postgres as the writer. It
exists because the MLflow `rossoctl-deps` installs runs mlflow-oidc-auth, which rejects both the
Service's read and the collector's write; the run then publishes zeros on every task, which reads
like missing telemetry rather than a refused read. It is a "reader" by convention only. Two of its
settings are measured rather than chosen: `--workers 1` (MLflow 3.x defaults to four) and a **4Gi**
limit. Gate on a 200 from `/api/2.0/mlflow/traces` rather than on the pod going Ready — two pip
installs run at container start — and pass `experiment_ids`, since that endpoint answers 400
without it. Probe from **inside** the pod (MLflow 3.x rejects the API server's service proxy as a
DNS-rebinding attempt) with `python`, not `curl`, which the image does not ship.
`reference/kind-post-setup.sh` does all of this, including pointing the collector at the reader;
by hand it is:

```bash
python3 reference/kind-collector-mlflow.py            # patch + restart; idempotent
python3 reference/kind-collector-mlflow.py --check    # report only, exit 1 if unpatched
```

**`ibac-judge.yaml`** — the upstream proxy the AuthBridge `ibac` plugin calls. The plugin's
ConfigMap is cleartext, so the key cannot live there; this holds it in a Secret and adds the
`Authorization` header outbound, leaving the plugin pointed at a credential-free in-cluster URL.
Without it the plugin is **inert**: it admits every call and the run looks like a clean pass, which
is why enforcement is proven by *counting judge calls* and never by reading a pass rate.
`UPSTREAM_BASE` in the committed copy is an `example.com` placeholder — substitute the real gateway
at apply time, and `rollout restart` after the key lands, since the proxy reads it once at pod
start.
