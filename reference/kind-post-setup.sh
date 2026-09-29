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
#   3. stand up the MLflow read path (mlflow-reader + point the collector at it)
#   4. generate the per-instance config + create the autobench-instances secret
#   5. deploy the Service (Deployment + Service + kind HTTPRoute), pinned to the image
#   6. verify /healthz
#
# Step 3 is not optional if you want token reports: without it the collector's export 401s against
# the OIDC-gated MLflow and every run publishes `model: "unknown"` with zero tokens, successfully.
#
# DEV/TEST ONLY. No secret is ever echoed. Secrets are resolved as:
#   KC_USER_PASSWORD   the `benchmarker` password. If unset, read from a chmod-600
#                      credentials file (default ~/.rossoctl-kind/benchmarker.pass,
#                      override with KC_CRED_FILE). The password does not change
#                      across upgrades, so create that file ONCE:
#                        umask 077; mkdir -p ~/.rossoctl-kind
#                        printf '%s' '<benchmarker password>' > ~/.rossoctl-kind/benchmarker.pass
#   KC_ADMIN_PASSWORD  Keycloak master admin password       (optional; auto-read
#                      from the in-cluster keycloak-initial-admin secret if unset)
#   BM_WORKLOAD_LLM_KEY  the workload LLM key, issued by the INTERNAL gateway. If unset,
#                      read from ~/.rossoctl-kind/litellm.key (chmod 600, override with
#                      KC_CRED_FILE's sibling LLM_KEY_FILE). Also does not change across
#                      upgrades, so create that file once the same way.
#                      NOT named OPENAI_API_KEY on purpose: that name is commonly exported in a
#                      developer's shell profile for an unrelated provider, and this script writes
#                      whatever it finds into cluster Secrets. A namespaced name cannot be
#                      inherited by accident. (Inside the pod the value still arrives as
#                      OPENAI_API_KEY — registry.py maps it from the Secret's `apikey`.)
set -euo pipefail
set +x  # never trace: keeps secrets out of the terminal

# --- config (env-overridable) ---
REFERENCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BENCH_REPO="${BENCH_REPO:-$(cd "$REFERENCE_DIR/.." && pwd)}"
IMAGE="${IMAGE:-ghcr.io/rossoctl/autobench:v1.29}"
CLUSTER="${CLUSTER:-rossoctl}"
CTX="${KUBE_CONTEXT:-kind-${CLUSTER}}"
REALM="${REALM:-rossoctl}"
CLIENT="${CLIENT:-rossoctl}"
KC_HOST="${KC_HOST:-keycloak.localtest.me:8080}"
KC_SERVER="${KC_SERVER:-http://${KC_HOST}}"
BENCH_USER="${BENCH_USER:-benchmarker}"
BENCH_EMAIL="${BENCH_EMAIL:-benchmarker@localtest.me}"
TEAM_NAMESPACES="${TEAM_NAMESPACES:-team1}"

# --- secrets (env, else chmod-600 file; admin pw falls back to the cluster secret) ---
KC_CRED_FILE="${KC_CRED_FILE:-$HOME/.rossoctl-kind/benchmarker.pass}"
if [ -z "${KC_USER_PASSWORD:-}" ] && [ -f "$KC_CRED_FILE" ]; then
  # refuse a world/group-readable credentials file
  perm="$(stat -f '%A' "$KC_CRED_FILE" 2>/dev/null || stat -c '%a' "$KC_CRED_FILE" 2>/dev/null || echo '')"
  case "$perm" in 600|400) ;; *) echo "refusing: $KC_CRED_FILE must be chmod 600 (is ${perm:-unknown})" >&2; exit 1 ;; esac
  IFS= read -r KC_USER_PASSWORD < "$KC_CRED_FILE" || true
fi
: "${KC_USER_PASSWORD:?set KC_USER_PASSWORD or create $KC_CRED_FILE (chmod 600) — never echoed}"
if [ -z "${KC_ADMIN_PASSWORD:-}" ]; then
  KC_ADMIN_PASSWORD="$(kubectl --context "$CTX" -n keycloak get secret keycloak-initial-admin \
    -o jsonpath='{.data.password}' 2>/dev/null | base64 -d || true)"
fi
: "${KC_ADMIN_PASSWORD:?could not read Keycloak admin password from keycloak-initial-admin; export KC_ADMIN_PASSWORD}"
export KC_ADMIN_PASSWORD KC_USER_PASSWORD
# The workload LLM key. KinD must use the INTERNAL gateway (`vpc-int`), which has its own key
# table — the key ykt2/ykt5 hold is issued by the external one and 401s here. Like the
# benchmarker password it does not change across rebuilds, so keep it in a chmod-600 file:
#   umask 077; printf '%s' '<key>' > ~/.rossoctl-kind/litellm.key
LLM_KEY_FILE="${LLM_KEY_FILE:-$HOME/.rossoctl-kind/litellm.key}"
if [ -z "${BM_WORKLOAD_LLM_KEY:-}" ] && [ -f "$LLM_KEY_FILE" ]; then
  perm="$(stat -f '%A' "$LLM_KEY_FILE" 2>/dev/null || stat -c '%a' "$LLM_KEY_FILE" 2>/dev/null || echo '')"
  case "$perm" in 600|400) ;; *) echo "refusing: $LLM_KEY_FILE must be chmod 600 (is ${perm:-unknown})" >&2; exit 1 ;; esac
  IFS= read -r BM_WORKLOAD_LLM_KEY < "$LLM_KEY_FILE" || true
fi

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
echo "==> ensuring the MLflow read path (mlflow-reader + collector export)"
kubectl --context "$CTX" apply -f "$BENCH_REPO/deploy/kind/mlflow-reader.yaml"
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
python3 "$REFERENCE_DIR/kind-collector-mlflow.py" --context "$CTX"

# --- 3. generate per-instance config + (re)create the autobench-instances secret ---
echo "==> generating instance config + secret"
export KC_SERVICE_USERNAME="$BENCH_USER" KC_SERVICE_PASSWORD="$KC_USER_PASSWORD"
OUT_DIR="$(mktemp -d)"; trap 'rm -rf "$OUT_DIR"' EXIT
# `s3` and `workload_llm` cannot be discovered from a cluster — which bucket credentials to publish
# with, and which gateway issued the key in openai-secret. Carry them over from the last generated
# file (instances/ is gitignored and survives a cluster rebuild), or they are dropped silently and
# the run publishes nothing.
COPY_FROM="${COPY_FROM:-$BENCH_REPO/instances/keycloak.localtest.me_8080.json}"
COPY_ARGS=()
if [ -f "$COPY_FROM" ]; then
  COPY_ARGS=(--copy-from "$COPY_FROM")
  echo "==> carrying s3 + workload_llm over from $(basename "$COPY_FROM")"
else
  echo "NOTE: no previous instance file at $COPY_FROM — the generated config will have no s3" >&2
  echo "      (artifacts unpublished) and no workload_llm (the agent uses the image default)." >&2
  echo "      Pass COPY_FROM=<file>, or --llm-base/--llm-model to kind-service-bootstrap.sh." >&2
fi
"$REFERENCE_DIR/kind-service-bootstrap.sh" \
  --cluster "$CLUSTER" --context "$CTX" --realm "$REALM" --client "$CLIENT" \
  --keycloak-host "$KC_HOST" --out-dir "$OUT_DIR" "${COPY_ARGS[@]}"
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
