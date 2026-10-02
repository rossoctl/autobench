#!/usr/bin/env bash
# Post-setup for a freshly (re)created LOCAL KinD rossoctl cluster. Run AFTER:
#     kind delete cluster --name rossoctl
#     scripts/kind/setup-rossoctl.sh --with-all --preload-images
# `kind delete cluster` destroys the whole node — the AutoBench Service, its
# instance-config secret, the loaded autobench image, and the `benchmarker`
# Keycloak user are ALL gone, and `--with-all` does NOT redeploy the Service.
# This script stands it all back up in one step:
#   1. build + kind-load the autobench image
#   2. re-seed the `benchmarker` Keycloak user (incl. the firstName/lastName the
#      realm requires for ROPC — keycloak-ensure-user.sh omits them)
#   3. stand up the MLflow read path (mlflow-reader + point the collector at it) — unless an MLflow
#      is already serving the configured tracking URL, which --install-mlflow decides
#   4. generate the per-instance config + create the autobench-instances secret
#   5. deploy the Service (Deployment + Service + kind HTTPRoute), pinned to the image
#   6. verify /healthz
#
# Step 3 is not optional if you want token reports: without it the collector's export 401s against
# the OIDC-gated MLflow and every run publishes `model: "unknown"` with zero tokens, successfully.
# What is optional is *installing* the MLflow — a cluster may already be pointed at one that exists
# (`--install-mlflow never`, or `auto` finding it), in which case this script only repoints the
# collector. Either way, prove the result with `autobench-cli mlflow-health`: "installed" and
# "accepts the spans we write" are different claims, and only the second one empties a report.
#
# DEV/TEST ONLY. No secret is ever echoed. Secrets are resolved as:
#   KC_USER_PASSWORD   the `benchmarker` password — from `--password-file`, `--password-stdin` or
#                      `--password` if given, else this variable, else a chmod-600 credentials file
#                      (default ~/.rossoctl-kind/benchmarker.pass, override with KC_CRED_FILE). The
#                      password does not change across upgrades, so create that file ONCE and no
#                      credential flag is ever needed:
#                        umask 077; mkdir -p ~/.rossoctl-kind
#                        printf '%s' '<benchmarker password>' > ~/.rossoctl-kind/benchmarker.pass
#                      Step 4 logs in with whatever this resolves to, because step 2 CANNOT: the
#                      realm refuses a token until the firstName/lastName patch below has run, so
#                      keycloak-ensure-user.sh is deliberately called without --verify.
#   KC_ADMIN_PASSWORD  Keycloak master admin password       (optional; auto-read
#                      from the in-cluster keycloak-initial-admin secret if unset)
#   BM_WORKLOAD_LLM_KEY  the workload LLM key, issued by the gateway this cluster can reach. If
#                      unset, read from the selected profile's file — ~/.rossoctl-llm/<profile>.key
#                      when LLM_PROFILE is set, else ~/.rossoctl-kind/litellm.key (chmod 600,
#                      override with LLM_KEY_FILE). Also does not change across upgrades, so create
#                      that file once the same way.
#   LLM_PROFILE        intranet|internet — where the LLM/LiteLLM service this cluster calls sits
#                      (see llm-profiles.sh). A kind cluster on the org intranet may use either and
#                      usually declares `intranet`; one on an Internet server must use `internet`.
#                      NOT named OPENAI_API_KEY on purpose: that name is commonly exported in a
#                      developer's shell profile for an unrelated provider, and this script writes
#                      whatever it finds into cluster Secrets. A namespaced name cannot be
#                      inherited by accident. (Inside the pod the value still arrives as
#                      OPENAI_API_KEY — registry.py maps it from the Secret's `apikey`.)
set -euo pipefail
set +x  # never trace: keeps secrets out of the terminal

# --- config (env-overridable) ---
REFERENCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Every --env-file is loaded BEFORE the defaults below read the environment, so a file beats the shell
# and a flag beats a file. The values are exported, so kind-service-bootstrap.sh inherits them.
# shellcheck source=reference/envfile.sh
. "$REFERENCE_DIR/envfile.sh"
envfile_prescan "$@" || exit 1
BENCH_REPO="${BENCH_REPO:-$(cd "$REFERENCE_DIR/.." && pwd)}"
IMAGE="${IMAGE:-ghcr.io/rossoctl/autobench:v1.32}"
CLUSTER="${CLUSTER:-rossoctl}"
CTX="${KUBE_CONTEXT:-kind-${CLUSTER}}"
REALM="${REALM:-rossoctl}"
CLIENT="${CLIENT:-rossoctl}"
KC_HOST="${KC_HOST:-keycloak.localtest.me:8080}"
KC_SERVER="${KC_SERVER:-http://${KC_HOST}}"
# KC_SERVICE_USERNAME / KC_SERVICE_PASSWORD are the names kind-service-bootstrap.sh and the env-file
# template use, so one file serves both scripts; BENCH_USER / KC_USER_PASSWORD still win when set.
BENCH_USER="${BENCH_USER:-${KC_SERVICE_USERNAME:-benchmarker}}"
KC_USER_PASSWORD="${KC_USER_PASSWORD:-${KC_SERVICE_PASSWORD:-}}"
KC_USER_PASSWORD_FILE="${KC_USER_PASSWORD_FILE:-${KC_SERVICE_PASSWORD_FILE:-}}"
BENCH_EMAIL="${BENCH_EMAIL:-benchmarker@localtest.me}"
TEAM_NAMESPACES="${TEAM_NAMESPACES:-team1}"
KC_CRED_FILE="${KC_CRED_FILE:-$HOME/.rossoctl-kind/benchmarker.pass}"
# Whether to install MLflow is a QUESTION, not a platform constant: on OpenShift it is pre-installed
# and on kind it usually is not, but a kind cluster can also be pointed at an existing one.
INSTALL_MLFLOW="${INSTALL_MLFLOW:-auto}"
# The URL the instance config will name. kind-service-bootstrap.sh defaults to the same address, and
# the two must agree — the Service reads the experiment the collector writes, and a split between
# them is silent (the run passes, every token count reads 0).
MLFLOW_URL="${MLFLOW_URL:-http://mlflow-reader.rossoctl-system.svc.cluster.local:5000}"

# --- flags ---
# Only the credential has flags; everything else stays env-only (see the header). Unknown arguments
# are an error rather than ignored, which is what they used to be — this script had no parser at all.
usage() {
  cat <<EOF
Usage: kind-post-setup.sh [flags]

Stands the AutoBench Service back up on a freshly recreated local kind cluster.

Required, from the environment or an --env-file: S3_ENABLED=true|false, and when true the S3_*
values (see reference/autobench.env.template). Checked before anything is built or applied.

Flags:
  --env-file FILE     KEY=VALUE lines (chmod 600), repeatable. Precedence, last wins: the shell,
                      then each --env-file in order, then flags.
  --username U        the Keycloak user to seed        (default: ${BENCH_USER}; env BENCH_USER)
  --password-file F   its password, from a chmod-600 file. The default is ${KC_CRED_FILE},
                      so with that file in place no credential flag is needed at all.
  --password-stdin    read the password from stdin
  --password P        the password on the command line — accepted, but argv is world-readable
                      via \`ps\`, and it lands in your shell history
  --install-mlflow M  auto|always|never   (default: ${INSTALL_MLFLOW}; env INSTALL_MLFLOW)
                      auto   — apply deploy/kind/mlflow-reader.yaml only when nothing is already
                               serving the configured tracking URL (env MLFLOW_URL, default the
                               in-cluster reader). A kind cluster pointed at an MLflow that already
                               exists keeps it: installing a second one splits the traces between
                               two databases, and reports then read the wrong half.
                      always — apply it regardless (the pre-2026-09-30 behaviour)
                      never  — apply nothing; MLflow is somebody else's, and its credential goes in
                               via kind-service-bootstrap.sh
  -h, --help

Everything else is environment-only: IMAGE, CLUSTER, KUBE_CONTEXT, REALM, CLIENT, KC_HOST,
TEAM_NAMESPACES, KC_ADMIN_PASSWORD, S3_*, LLM_PROFILE, LLM_KEY_FILE, BM_WORKLOAD_LLM_KEY, MLFLOW_URL
(the tracking URL the instance config names — also what --install-mlflow auto looks for).
EOF
}
CRED_PASSWORD_FILE=""
CRED_PASSWORD_STDIN=""
CRED_PASSWORD_ARGV=""
while [ $# -gt 0 ]; do
  case "$1" in
    --username)       BENCH_USER="$2"; shift 2 ;;
    --password-file)  CRED_PASSWORD_FILE="$2"; shift 2 ;;
    --password-stdin) CRED_PASSWORD_STDIN=1; shift ;;
    --password)       CRED_PASSWORD_ARGV="$2"; shift 2 ;;
    --install-mlflow) INSTALL_MLFLOW="$2"; shift 2 ;;
    --env-file)       shift 2 ;;   # already loaded by envfile_prescan
    --env-file=*)     shift ;;
    -h|--help)        usage; exit 0 ;;
    *)                usage; echo "Error: unknown argument '$1'" >&2; exit 1 ;;
  esac
done
case "$INSTALL_MLFLOW" in
  auto|always|never) ;;
  *) echo "Error: --install-mlflow must be auto, always or never (got '$INSTALL_MLFLOW')" >&2; exit 1 ;;
esac

# S3 is declared, and checked here — before the image build and the Keycloak seeding — rather than only
# in kind-service-bootstrap.sh, which runs after both and would fail with the cluster half set up.
export S3_ENABLED S3_BUCKET S3_REGION S3_ACCESS_KEY_ID S3_SECRET_ACCESS_KEY S3_ENDPOINT_URL S3_PREFIX
python3 "$REFERENCE_DIR/s3check.py" ${SKIP_S3_CHECK:+--offline} \
  || { echo "Error: S3 is not usable as declared (see above) — fix the S3_* values, or declare S3_ENABLED=false" >&2; exit 1; }

# --- secrets (flags, else env, else chmod-600 file; admin pw falls back to the cluster secret) ---
# shellcheck source=reference/credfile.sh
. "$REFERENCE_DIR/credfile.sh"
CRED_N=0
[ -n "$CRED_PASSWORD_FILE" ]  && CRED_N=$((CRED_N+1))
[ -n "$CRED_PASSWORD_STDIN" ] && CRED_N=$((CRED_N+1))
[ -n "$CRED_PASSWORD_ARGV" ]  && CRED_N=$((CRED_N+1))
[ "$CRED_N" -le 1 ] || { echo "Error: --password-file, --password-stdin and --password are mutually exclusive" >&2; exit 1; }
# The default credentials file is the last resort, so a flag or an export always wins over it — and
# it is only reached for when it EXISTS, so an absent one falls through to the message below that
# names all four routes rather than complaining about one path you never asked for.
[ "$CRED_N" != 0 ] || [ -n "${KC_USER_PASSWORD:-}" ] || [ -n "${KC_USER_PASSWORD_FILE:-}" ] \
  || [ ! -f "$KC_CRED_FILE" ] \
  || CRED_PASSWORD_FILE="$KC_CRED_FILE"
CRED_RC=0; cred_resolve_password KC_USER_PASSWORD || CRED_RC=$?   # `; rc=$?` would trip set -e first
case "$CRED_RC" in
  0) ;;
  2) echo "Error: no password for ${BENCH_USER}: pass --password-file <chmod-600 file>, --password-stdin, --password, set KC_USER_PASSWORD, or create $KC_CRED_FILE" >&2; exit 1 ;;
  *) exit 1 ;;   # cred_read_file already said why
esac
echo "==> ${BENCH_USER} password from ${CRED_PASSWORD_SOURCE}"
if [ -z "${KC_ADMIN_PASSWORD:-}" ]; then
  KC_ADMIN_PASSWORD="$(kubectl --context "$CTX" -n keycloak get secret keycloak-initial-admin \
    -o jsonpath='{.data.password}' 2>/dev/null | base64 -d || true)"
fi
: "${KC_ADMIN_PASSWORD:?could not read Keycloak admin password from keycloak-initial-admin; export KC_ADMIN_PASSWORD}"
export KC_ADMIN_PASSWORD KC_USER_PASSWORD
# The workload LLM key, issued by the LLM/LiteLLM service this cluster is configured to call. Every
# service has its own key table, so a key issued by another one 401s here. Like the
# benchmarker password it does not change across rebuilds, so keep it in a chmod-600 file:
#   umask 077; printf '%s' '<key>' > ~/.rossoctl-kind/litellm.key
# Set LLM_PROFILE=intranet|internet and the key comes from that profile's own file
# (~/.rossoctl-llm/<profile>.key), along with the api_base and model the instance file will carry —
# see llm-profiles.sh. Per-profile files are the point: the two gateways keep separate key tables, so
# a single file holding "the" key is exactly how a key gets used against the gateway that never
# issued it. With no profile set, the legacy per-cluster path still works unchanged.
# shellcheck source=reference/llm-profiles.sh
. "$REFERENCE_DIR/llm-profiles.sh"
llm_profile_resolve || exit 1
LEGACY_LLM_KEY_FILE="$HOME/.rossoctl-kind/litellm.key"
[ -n "${LLM_KEY_FILE:-}" ] || LLM_KEY_FILE="$LEGACY_LLM_KEY_FILE"
llm_profile_load_key || exit 1
if [ -z "${BM_WORKLOAD_LLM_KEY:-}" ] && [ "$LLM_KEY_FILE" != "$LEGACY_LLM_KEY_FILE" ] \
   && [ -f "$LEGACY_LLM_KEY_FILE" ]; then
  echo "NOTE: $LLM_KEY_FILE is absent; falling back to $LEGACY_LLM_KEY_FILE" >&2
  LLM_KEY_FILE="$LEGACY_LLM_KEY_FILE"
  llm_profile_load_key || exit 1
fi
export LLM_PROFILE   # kind-service-bootstrap.sh writes the matching api_base/model/no_proxy

# --- 0. safety: act on the kind cluster only ---
kubectl config use-context "$CTX" >/dev/null
kubectl --context "$CTX" get ns rossoctl-system >/dev/null
echo "==> context $CTX, target image $IMAGE"

# --- 1. build + load the autobench image (single-arch local; IfNotPresent) ---
echo "==> building $IMAGE"
( cd "$BENCH_REPO" && docker build --load -t "$IMAGE" . )
echo "==> loading into kind cluster $CLUSTER"
kind load docker-image "$IMAGE" --name "$CLUSTER"

# --- 2. re-seed benchmarker, then set the realm-required firstName/lastName ---
echo "==> ensuring Keycloak user '$BENCH_USER'"
"$REFERENCE_DIR/keycloak-ensure-user.sh" \
  --server "$KC_SERVER" --realm "$REALM" --client "$CLIENT" \
  --username "$BENCH_USER" --email "$BENCH_EMAIL"
# keycloak-ensure-user.sh creates {username,enabled,email,emailVerified} only; the
# rossoctl realm's user-profile also requires firstName+lastName, else ROPC 400s
# "Account is not fully set up". Patch them via the Admin REST API.
ATOK="$(curl -sS "$KC_SERVER/realms/master/protocol/openid-connect/token" \
  -d client_id=admin-cli -d grant_type=password -d username=admin \
  --data-urlencode "password=${KC_ADMIN_PASSWORD}" | jq -r '.access_token')"
[ -n "$ATOK" ] && [ "$ATOK" != null ] || { echo "admin token failed" >&2; exit 1; }
USER_ID="$(curl -sS -H "Authorization: Bearer $ATOK" \
  "$KC_SERVER/admin/realms/$REALM/users?username=$BENCH_USER&exact=true" | jq -r '.[0].id')"
curl -sS -o /dev/null -w '' -X PUT -H "Authorization: Bearer $ATOK" -H 'Content-Type: application/json' \
  "$KC_SERVER/admin/realms/$REALM/users/$USER_ID" \
  -d "{\"firstName\":\"Bench\",\"lastName\":\"Marker\",\"email\":\"${BENCH_EMAIL}\",\"emailVerified\":true,\"requiredActions\":[]}"
echo "==> user profile patched (firstName/lastName/email)"
# The realm import does NOT grant benchmarker the operator role, but every Service call that
# creates workloads needs it: POST /api/v1/tools otherwise 403s "Required role(s):
# rossoctl-operator" and the Service surfaces that as a 502 on /deploy. Grant it idempotently
# (re-POSTing an existing mapping is a no-op).
BENCH_ROLE="${BENCH_ROLE:-rossoctl-operator}"
ROLE_JSON="$(curl -sS -H "Authorization: Bearer $ATOK" \
  "$KC_SERVER/admin/realms/$REALM/roles/$BENCH_ROLE")"
if [ -n "$ROLE_JSON" ] && [ "$(printf '%s' "$ROLE_JSON" | jq -r '.id // empty')" != "" ]; then
  curl -sS -o /dev/null -X POST -H "Authorization: Bearer $ATOK" -H 'Content-Type: application/json' \
    "$KC_SERVER/admin/realms/$REALM/users/$USER_ID/role-mappings/realm" -d "[${ROLE_JSON}]"
  echo "==> granted realm role '$BENCH_ROLE' to '$BENCH_USER'"
else
  echo "WARNING: realm role '$BENCH_ROLE' not found in realm '$REALM' — /deploy will 502 with a 403" >&2
fi

# --- 2b. workload secrets the benchmarks reference by name ---
# registry.py injects HF_TOKEN from secret `hf-secret` (key `hf-token`) and OPENAI_API_KEY from
# `openai-secret` (key `apikey`). A MISSING secret is fatal: the MCP pod sits in
# CreateContainerConfigError ("secret \"hf-secret\" not found") and the agent then crash-loops
# unable to reach it. hf-token is EMPTY on ykt2 too (the gsm8k dataset is public) — the secret
# only has to exist. The LLM key is real and must be supplied out-of-band: export
# BM_WORKLOAD_LLM_KEY, else it is left untouched (an existing empty apikey means every run 401s).
#
# A fresh `--with-all` install creates NEITHER secret in team1/team2, so this has to
# create-or-patch: `apply` alone would clobber an existing key with an empty one. The key value
# reaches kubectl over stdin, never on argv (printf is a shell builtin, so no process ever carries
# it) — `ps` would otherwise expose it.
ensure_apikey() {  # $1=namespace $2=secret name
  kubectl --context "$CTX" -n "$1" get secret "$2" >/dev/null 2>&1 || {
    printf 'apiVersion: v1\nkind: Secret\nmetadata:\n  name: %s\ntype: Opaque\ndata: {}\n' "$2" \
      | kubectl --context "$CTX" -n "$1" apply -f - >/dev/null
    echo "==> created empty $2 in $1"
  }
  [ -n "${BM_WORKLOAD_LLM_KEY:-}" ] || return 0
  { printf '{"data":{"apikey":"'
    printf '%s' "$BM_WORKLOAD_LLM_KEY" | base64 | tr -d '\n'
    printf '"}}'
  } | kubectl --context "$CTX" -n "$1" patch secret "$2" --type merge --patch-file /dev/stdin >/dev/null \
    && echo "==> $2 apikey set in $1" \
    || echo "WARNING: could not patch $2 in $1" >&2
}
# Only the namespaces deployed into: every spec in reference/run12_specs.json names team1. Override
# with TEAM_NAMESPACES when a run targets another namespace — the secrets are per-namespace, and a
# leg landing where they are absent crash-loops rather than failing cleanly.
for ns in $TEAM_NAMESPACES; do
  kubectl --context "$CTX" -n "$ns" create secret generic hf-secret \
    --from-literal=hf-token="" --dry-run=client -o yaml | kubectl --context "$CTX" apply -f - >/dev/null
  echo "==> hf-secret ensured in $ns"
  ensure_apikey "$ns" openai-secret
done
# The ibac judge proxy is a SECOND slot for the same key (env UPSTREAM_KEY) and was missed on the
# first pass last time. Only patch it if the judge is installed — it is optional on KinD.
if kubectl --context "$CTX" -n rossoctl-system get deploy ibac-judge >/dev/null 2>&1; then
  ensure_apikey rossoctl-system ibac-judge-upstream
  [ -n "${BM_WORKLOAD_LLM_KEY:-}" ] && kubectl --context "$CTX" -n rossoctl-system \
    rollout restart deploy/ibac-judge >/dev/null && echo "==> restarted ibac-judge (reads the key at pod start)"
fi
if [ -z "${BM_WORKLOAD_LLM_KEY:-}" ]; then
  echo "NOTE: no LLM key (env BM_WORKLOAD_LLM_KEY unset, no $LLM_KEY_FILE) — secrets exist but are empty," >&2
  echo "      so MCP pods start and every completion 401s. Install the key, then re-run this script." >&2
fi

# --- 2c. the MLflow read path (without it every token count in every report reads 0) ---
# Two halves, and both have to point at the same place: mlflow-reader serves the writer's postgres
# with no auth, and the collector has to export to IT rather than to the OIDC-gated `mlflow` that
# rossoctl-deps installs. Getting this wrong is silent — the run passes, `model` is "unknown", and
# every token count is 0, which reads like an agent that emitted no telemetry.
#
# WHETHER to install it is asked, not assumed (--install-mlflow). `auto` looks for a Service already
# serving the configured tracking URL: if one is there, installing our reader beside it would write
# the traces to one database and read them from the other, which looks exactly like an agent that
# emitted no telemetry. The Service OBJECT is what is checked, because that is all that is knowable
# from the host — whether it ANSWERS is the round-trip probe (reference/preflight.py, or
# `autobench-cli mlflow-health`), and "installed" and "answering" are different claims.
MLFLOW_HOSTPORT="${MLFLOW_URL#*://}"; MLFLOW_HOSTPORT="${MLFLOW_HOSTPORT%%/*}"
MLFLOW_SVC="${MLFLOW_HOSTPORT%%.*}"
MLFLOW_NS="${MLFLOW_HOSTPORT#*.}"; MLFLOW_NS="${MLFLOW_NS%%.*}"
[ "$MLFLOW_NS" = "$MLFLOW_SVC" ] && MLFLOW_NS=rossoctl-system   # a bare host, no svc DNS suffix
DO_INSTALL_MLFLOW=1
case "$INSTALL_MLFLOW" in
  never)  DO_INSTALL_MLFLOW=0
          echo "==> MLflow install skipped (--install-mlflow never): using ${MLFLOW_URL}" ;;
  always) echo "==> installing mlflow-reader (--install-mlflow always)" ;;
  auto)   if kubectl --context "$CTX" -n "$MLFLOW_NS" get svc "$MLFLOW_SVC" >/dev/null 2>&1; then
            DO_INSTALL_MLFLOW=0
            echo "==> reusing the MLflow already serving ${MLFLOW_URL} (svc ${MLFLOW_SVC} in ${MLFLOW_NS})"
            echo "    installing a second one would split the traces; pass --install-mlflow always to force"
          else
            echo "==> nothing serves ${MLFLOW_URL} yet; installing mlflow-reader"
          fi ;;
esac
if [ "$DO_INSTALL_MLFLOW" = 1 ]; then
  if [ "$MLFLOW_SVC" != "mlflow-reader" ]; then
    # Applying our reader would not make the configured URL resolve, so the run would still publish
    # an empty report — with an installed MLflow nobody reads sitting next to it.
    echo "Error: MLFLOW_URL names svc ${MLFLOW_SVC} in ${MLFLOW_NS}, which does not exist, and" >&2
    echo "       deploy/kind/mlflow-reader.yaml serves mlflow-reader in rossoctl-system — installing" >&2
    echo "       it would not make that URL answer. Create that MLflow, or drop MLFLOW_URL." >&2
    exit 1
  fi
  kubectl --context "$CTX" apply -f "$BENCH_REPO/deploy/kind/mlflow-reader.yaml"
fi
# The rollout wait and the API probe run whenever the reader is present — including when this run did
# not install it — because being installed is not the same as answering. They exec INTO that pod, so
# an MLflow provided from elsewhere has no equivalent here and is deferred to the round-trip probe.
if ! kubectl --context "$CTX" -n rossoctl-system get deploy mlflow-reader >/dev/null 2>&1; then
  echo "==> no deploy/mlflow-reader to probe; verify ${MLFLOW_URL} with \`autobench-cli mlflow-health\`"
  echo "    after step 6 — it is the only check that proves the write half of the path"
else
  kubectl --context "$CTX" -n rossoctl-system rollout status deploy/mlflow-reader --timeout=300s
  # Gate on the API, not on the pod: two pip installs run at container start, so Ready precedes
  # usable by a wide margin. Three details make this the only probe that works: the query runs from
  # INSIDE the pod (MLflow 3.x rejects the API server's service proxy as a DNS-rebinding attempt),
  # `experiment_ids` is required (without it the endpoint answers 400, which reads like a broken
  # server), and the image ships no curl — python is what is there.
  MLFLOW_PROBE="import urllib.request;urllib.request.urlopen('http://localhost:5000/api/2.0/mlflow/traces?experiment_ids=0&max_results=1',timeout=10)"
  MLFLOW_READY=0
  for _ in $(seq 1 30); do
    if kubectl --context "$CTX" -n rossoctl-system exec deploy/mlflow-reader -- \
        python -c "$MLFLOW_PROBE" >/dev/null 2>&1; then
      MLFLOW_READY=1; break
    fi
    sleep 5
  done
  if [ "$MLFLOW_READY" = 1 ]; then
    echo "==> mlflow-reader answers /api/2.0/mlflow/traces"
  else
    echo "WARNING: mlflow-reader is Ready but its traces API is not answering — token reports will be" >&2
    echo "         empty. Check the container's lastState: one MLflow 3.x worker idles at ~2.3 GiB, so" >&2
    echo "         an OOMKill leaves a log ending on 'Application startup complete' with no error." >&2
  fi
fi
# The collector must export to the MLflow the Service reads, whoever installed it — so the URL comes
# from MLFLOW_URL rather than from this script's default, which is the same address anyway unless an
# existing MLflow was named.
# shellcheck source=reference/yamlpy.sh
. "$REFERENCE_DIR/yamlpy.sh"
yaml_python "$(dirname "$REFERENCE_DIR")" || { echo "Error: $YAML_PYTHON_HINT" >&2; exit 1; }
"${PY[@]}" "$REFERENCE_DIR/kind-collector-mlflow.py" --context "$CTX" \
  --reader-url "${MLFLOW_URL%/}/v1/traces"

# --- 3. generate per-instance config + (re)create the autobench-instances secret ---
echo "==> generating instance config + secret"
export KC_SERVICE_USERNAME="$BENCH_USER" KC_SERVICE_PASSWORD="$KC_USER_PASSWORD"
unset KC_SERVICE_PASSWORD_FILE   # resolved above; left set, the bootstrap would see two sources
OUT_DIR="$(mktemp -d)"; trap 'rm -rf "$OUT_DIR"' EXIT
# `workload_llm` cannot be discovered from a cluster — which gateway issued the key in openai-secret.
# Carry it over from the last generated file (instances/ is gitignored and survives a cluster
# rebuild), or it is dropped silently. S3 is NOT carried: it comes from the S3_* declaration above.
COPY_FROM="${COPY_FROM:-$BENCH_REPO/instances/keycloak.localtest.me_8080.json}"
COPY_ARGS=()
if [ -f "$COPY_FROM" ]; then
  COPY_ARGS=(--copy-from "$COPY_FROM")
  echo "==> carrying workload_llm over from $(basename "$COPY_FROM")"
else
  echo "NOTE: no previous instance file at $COPY_FROM — the generated config will have no" >&2
  echo "      workload_llm (the agent uses the image default)." >&2
  echo "      Pass COPY_FROM=<file>, or --llm-base/--llm-model to kind-service-bootstrap.sh." >&2
fi
"$REFERENCE_DIR/kind-service-bootstrap.sh" \
  --cluster "$CLUSTER" --context "$CTX" --realm "$REALM" --client "$CLIENT" \
  --keycloak-host "$KC_HOST" --out-dir "$OUT_DIR" --mlflow-url "$MLFLOW_URL" "${COPY_ARGS[@]}"
FROM_FILE_ARGS=()
for f in "$OUT_DIR"/*.json; do FROM_FILE_ARGS+=(--from-file="$(basename "$f")=$f"); done
kubectl --context "$CTX" -n rossoctl-system create secret generic autobench-instances \
  "${FROM_FILE_ARGS[@]}" --dry-run=client -o yaml | kubectl --context "$CTX" apply -f -

# --- 4. deploy the Service (image pinned to $IMAGE) ---
echo "==> deploying autobench-service"
kubectl --context "$CTX" apply -f "$BENCH_REPO/deploy/service.yaml"
kubectl --context "$CTX" apply -f "$BENCH_REPO/deploy/kind/httproute.yaml"
kubectl --context "$CTX" apply -f "$BENCH_REPO/deploy/deployment.yaml"
kubectl --context "$CTX" -n rossoctl-system set image deploy/autobench-service \
  autobench-service="$IMAGE"
kubectl --context "$CTX" -n rossoctl-system rollout status deploy/autobench-service --timeout=180s

# --- 5. verify ---
echo "==> verifying http://autobench.localtest.me:8080/healthz"
if curl -fsS http://autobench.localtest.me:8080/healthz >/dev/null; then
  echo "OK — AutoBench Service is up at http://autobench.localtest.me:8080"
else
  echo "healthz not reachable yet (gateway may still be admitting the route); retry shortly" >&2
fi
