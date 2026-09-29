#!/usr/bin/env bash
# Host-side dev helper for a LOCAL kind deployment of Rossoctl (DEV/TEST ONLY —
# kind is not a production platform).
#
# In kind the AutoBench Service is deployed IN-CLUSTER (as a pod alongside
# Rossoctl), not as a host Podman container: kind's loopback-only *.localtest.me
# issuer/API cannot be reached off-host. NOTE (verified): there is NO *.localtest.me
# CoreDNS rewrite, and in-cluster those names resolve to POD LOOPBACK; the gateway
# serves :80, not :8080. So the in-cluster Service reaches Rossoctl/Keycloak over
# SERVICE DNS, keeping the public issuer (iss, :8080) separate from the backchannel
# URL (svc DNS) — exactly as rossoctl-backend does (KEYCLOAK_PUBLIC_URL vs
# KEYCLOAK_URL). See SERVICE_DESIGN_DECISIONS.md ("deploy the Service in-cluster").
#
# This script generates the per-instance config bundle for that in-cluster
# deployment (instances/<encoded-iss-host>.json): the exact iss (public, for
# identity matching) discovered from Keycloak's .well-known via the host ingress,
# plus in-cluster service-DNS URLs for Rossoctl + the Keycloak backchannel + MLflow.
# Mount the result into the Service pod as a Secret/ConfigMap. It does NOT deploy
# anything.
#
# Uses curl + jq + kubectl only (no kcadm.sh / oc). Safe to re-run.
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: kind-service-bootstrap.sh [flags]

Generates instances/<iss-host>.json for the local kind Rossoctl instance, to be
mounted into the in-cluster AutoBench Service pod as a Secret/ConfigMap.
DEV/TEST ONLY — kind is not a production platform.

Flags (each also has an env fallback):
  --cluster NAME       KIND_CLUSTER_NAME  kind cluster name        (default: rossoctl)
  --context NAME       KUBE_CONTEXT       kubectl context          (default: kind-<cluster>)
  --realm NAME         KC_REALM           Keycloak realm           (default: rossoctl)
  --keycloak-host H    KC_HOST            Keycloak ingress host (for iss discovery only)
                                          (default: keycloak.localtest.me:8080)
  --kc-backchannel URL KC_BACKCHANNEL_URL in-cluster Keycloak base URL the Service dials for
                                          JWKS + ROPC token (default: http://keycloak-service.keycloak:8080)
  --rossoctl-url URL   ROSSOCTL_BASE_URL  in-cluster Rossoctl API base URL
                                          (default: http://rossoctl-backend.rossoctl-system:8000)
  --mlflow-namespace N MLFLOW_NAMESPACE   MLflow namespace          (default: rossoctl-system)
  --mlflow-url URL     MLFLOW_URL         MLflow tracking URL (default: the no-auth mlflow-reader
                                          in-cluster svc URL — see the note below)
  --experiment-id N    MLFLOW_EXPERIMENT_ID  (default: READ from the collector's own exporter
                                          header, because guessing it yields a zero-token report)
  --copy-from FILE     COPY_FROM          existing instance JSON to carry `s3` and `workload_llm`
                                          over from. Those two are environment facts this script
                                          cannot discover, and a cluster rebuild should reproduce
                                          them rather than lose them.
  --llm-base URL       WORKLOAD_LLM_BASE  LLM gateway base (kind needs the INTERNAL one)
  --llm-model M        WORKLOAD_LLM_MODEL default model, e.g. openai/<gateway model id>
  --otel-endpoint URL  WORKLOAD_OTEL_ENDPOINT  (default: the in-cluster collector on :8335)
  --agent-runner R     WORKLOAD_AGENT_RUNNER   (default: service — `direct` emits no agent spans,
                                          so every token count reads 0)
  --s3-prefix P        S3_PREFIX          (default: kind/)
  --out-dir DIR        OUT_DIR            where to write the config (default: ./instances)
  --kubectl BIN        KUBECTL_BIN        kubectl binary            (default: kubectl)
  --client ID          KC_SERVICE_CLIENT_ID  client the Service logs in through (default: rossoctl)
  -h, --help

MLflow on kind: the `mlflow` Deployment rossoctl-deps installs runs mlflow-oidc-auth, which
refuses both the Service's read and the collector's write. deploy/kind/mlflow-reader.yaml serves
the same postgres with no auth, and this script points the Service at it by default. Wire the
collector to it too, or spans are dropped with a 401 and the run publishes zeros:
  python3 reference/kind-collector-mlflow.py

The Service authenticates to Rossoctl with ITS OWN per-instance credential
(ROPC / password grant). In kind the login/JWKS URLs are taken from the Keycloak
BACKCHANNEL URL (not composed from iss — the iss host is unreachable in-cluster);
iss is stored for identity matching only. That credential is the `benchmarker`
identity — per-instance by design, seeded here as a deploy-time default and
overridable at runtime via the benchmarker config API. The generated file
therefore carries it. Secrets are read from the environment, never the
command line:
  KC_SERVICE_USERNAME        benchmarker login username     (required)
  KC_SERVICE_PASSWORD        benchmarker login password     (required)
  KC_SERVICE_CLIENT_SECRET   client secret, if confidential (optional)
No cluster credential is written — Rossoctl performs cluster ops server-side.
EOF
}

log()  { printf '%s\n' "$*" >&2; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'Error: %s\n' "$*" >&2; exit 1; }

# --- dependencies ---
command -v curl    >/dev/null || die "curl is required"
command -v jq      >/dev/null || die "jq is required"

# --- defaults / env fallbacks ---
KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-rossoctl}"
KUBE_CONTEXT="${KUBE_CONTEXT:-}"
KC_REALM="${KC_REALM:-rossoctl}"
KC_HOST="${KC_HOST:-keycloak.localtest.me:8080}"
KC_BACKCHANNEL_URL="${KC_BACKCHANNEL_URL:-http://keycloak-service.keycloak:8080}"
ROSSOCTL_BASE_URL="${ROSSOCTL_BASE_URL:-http://rossoctl-backend.rossoctl-system:8000}"
MLFLOW_NAMESPACE="${MLFLOW_NAMESPACE:-rossoctl-system}"
MLFLOW_URL="${MLFLOW_URL:-}"
MLFLOW_EXPERIMENT_ID="${MLFLOW_EXPERIMENT_ID:-}"
COPY_FROM="${COPY_FROM:-}"
WORKLOAD_LLM_BASE="${WORKLOAD_LLM_BASE:-}"
WORKLOAD_LLM_MODEL="${WORKLOAD_LLM_MODEL:-}"
WORKLOAD_OTEL_ENDPOINT="${WORKLOAD_OTEL_ENDPOINT:-http://otel-collector.rossoctl-system.svc.cluster.local:8335}"
WORKLOAD_AGENT_RUNNER="${WORKLOAD_AGENT_RUNNER:-service}"
S3_PREFIX="${S3_PREFIX:-kind/}"
OUT_DIR="${OUT_DIR:-./instances}"
KUBECTL_BIN="${KUBECTL_BIN:-kubectl}"
KC_SERVICE_CLIENT_ID="${KC_SERVICE_CLIENT_ID:-rossoctl}"
KC_SERVICE_USERNAME="${KC_SERVICE_USERNAME:-}"
KC_SERVICE_PASSWORD="${KC_SERVICE_PASSWORD:-}"
KC_SERVICE_CLIENT_SECRET="${KC_SERVICE_CLIENT_SECRET:-}"

# --- flag parsing ---
while [ $# -gt 0 ]; do
    case "$1" in
        --cluster)          KIND_CLUSTER_NAME="$2"; shift 2 ;;
        --context)          KUBE_CONTEXT="$2"; shift 2 ;;
        --realm)            KC_REALM="$2"; shift 2 ;;
        --keycloak-host)    KC_HOST="$2"; shift 2 ;;
        --kc-backchannel)   KC_BACKCHANNEL_URL="$2"; shift 2 ;;
        --rossoctl-url)     ROSSOCTL_BASE_URL="$2"; shift 2 ;;
        --mlflow-namespace) MLFLOW_NAMESPACE="$2"; shift 2 ;;
        --mlflow-url)       MLFLOW_URL="$2"; shift 2 ;;
        --experiment-id)    MLFLOW_EXPERIMENT_ID="$2"; shift 2 ;;
        --copy-from)        COPY_FROM="$2"; shift 2 ;;
        --llm-base)         WORKLOAD_LLM_BASE="$2"; shift 2 ;;
        --llm-model)        WORKLOAD_LLM_MODEL="$2"; shift 2 ;;
        --otel-endpoint)    WORKLOAD_OTEL_ENDPOINT="$2"; shift 2 ;;
        --agent-runner)     WORKLOAD_AGENT_RUNNER="$2"; shift 2 ;;
        --s3-prefix)        S3_PREFIX="$2"; shift 2 ;;
        --out-dir)          OUT_DIR="$2"; shift 2 ;;
        --kubectl)          KUBECTL_BIN="$2"; shift 2 ;;
        --client)           KC_SERVICE_CLIENT_ID="$2"; shift 2 ;;
        -h|--help)          usage; exit 0 ;;
        *)                  usage; die "unknown argument '$1'" ;;
    esac
done

command -v "$KUBECTL_BIN" >/dev/null || die "$KUBECTL_BIN is required"
[ -n "$KC_SERVICE_USERNAME" ] || die "KC_SERVICE_USERNAME must be set in the environment (Service ROPC login username)"
[ -n "$KC_SERVICE_PASSWORD" ] || die "KC_SERVICE_PASSWORD must be set in the environment (Service ROPC login password)"
[ -n "$KUBE_CONTEXT" ] || KUBE_CONTEXT="kind-${KIND_CLUSTER_NAME}"

kc() { "$KUBECTL_BIN" --context "$KUBE_CONTEXT" "$@"; }

# req METHOD URL -> sets BODY / HTTP_STATUS
BODY=""; HTTP_STATUS=""
req() {
    local method="$1" url="$2"; shift 2
    local out
    out="$(curl -sS -w $'\n%{http_code}' -X "$method" "$url" "$@" 2>/dev/null)" || { BODY=""; HTTP_STATUS="000"; return 0; }
    HTTP_STATUS="${out##*$'\n'}"
    BODY="${out%$'\n'*}"
}

# --- 1. resolve issuer (authoritative from .well-known; fallback to convention) ---
log "==> Resolving issuer from Keycloak (${KC_HOST}, realm ${KC_REALM})..."
ISS=""
req GET "http://${KC_HOST}/realms/${KC_REALM}/.well-known/openid-configuration"
if [ "$HTTP_STATUS" = "200" ]; then
    ISS="$(printf '%s' "$BODY" | jq -r '.issuer // empty')"
fi
if [ -z "$ISS" ]; then
    ISS="http://${KC_HOST}/realms/${KC_REALM}"
    warn "could not fetch .well-known (HTTP ${HTTP_STATUS}); using constructed iss ${ISS}"
else
    log "    iss = ${ISS}"
fi

# --- 2. read MLflow client-credentials from mlflow-oauth-secret (default) ---
log "==> Reading mlflow-oauth-secret from namespace ${MLFLOW_NAMESPACE}..."
MLFLOW_CLIENT_ID=""; MLFLOW_CLIENT_SECRET=""; MLFLOW_TOKEN_URL=""
secret_json="$(kc -n "$MLFLOW_NAMESPACE" get secret mlflow-oauth-secret -o json 2>/dev/null || true)"
if [ -n "$secret_json" ]; then
    MLFLOW_CLIENT_ID="$(printf '%s' "$secret_json"     | jq -r '.data["OIDC_CLIENT_ID"] // empty'     | base64 -d 2>/dev/null || true)"
    MLFLOW_CLIENT_SECRET="$(printf '%s' "$secret_json" | jq -r '.data["OIDC_CLIENT_SECRET"] // empty' | base64 -d 2>/dev/null || true)"
    MLFLOW_TOKEN_URL="$(printf '%s' "$secret_json"     | jq -r '.data["OIDC_TOKEN_URL"] // empty'     | base64 -d 2>/dev/null || true)"
    log "    mlflow client-credentials read"
else
    warn "mlflow-oauth-secret not found in ${MLFLOW_NAMESPACE}; MLflow creds left empty"
fi

# kind does NOT expose MLflow via ingress, but the Service runs IN-CLUSTER, so the
# in-cluster svc URL is directly reachable (no port-forward / ingress needed —
# this closes inventory item #7 for kind). Override with --mlflow-url only if
# MLflow lives elsewhere.
#
# It defaults to mlflow-READER, not to `mlflow`. The Deployment rossoctl-deps installs runs
# mlflow-oidc-auth and rejects the Service's bearer, and the failure is silent: every run
# succeeds and every token count reads 0. deploy/kind/mlflow-reader.yaml serves the same
# postgres with no auth.
if [ -z "$MLFLOW_URL" ]; then
    MLFLOW_URL="http://mlflow-reader.${MLFLOW_NAMESPACE}.svc.cluster.local:5000"
    log "    MLflow tracking URL defaulted to the no-auth reader ${MLFLOW_URL}"
    if ! kc -n "$MLFLOW_NAMESPACE" get deploy mlflow-reader >/dev/null 2>&1; then
        warn "mlflow-reader is NOT deployed in ${MLFLOW_NAMESPACE} — apply deploy/kind/mlflow-reader.yaml,"
        warn "  or token reports will be empty. (Pass --mlflow-url to use a different MLflow.)"
    fi
fi

# The experiment id is READ from the collector's own exporter header, never chosen: an instance
# config that names an experiment nothing writes to produces exactly the same zero-token report
# as a refused write, so a guess here is indistinguishable from a broken telemetry chain.
if [ -z "$MLFLOW_EXPERIMENT_ID" ]; then
    MLFLOW_EXPERIMENT_ID="$(python3 "$(dirname "${BASH_SOURCE[0]}")/kind-collector-mlflow.py" \
        --context "$KUBE_CONTEXT" --print-experiment-id 2>/dev/null || true)"
    if [ -n "$MLFLOW_EXPERIMENT_ID" ]; then
        log "    experiment id ${MLFLOW_EXPERIMENT_ID} read from the collector's MLflow exporter"
    else
        MLFLOW_EXPERIMENT_ID="0"
        warn "could not read the collector's x-mlflow-experiment-id; defaulting to 0"
    fi
fi

# `s3` and `workload_llm` are environment facts this script cannot discover — which gateway issued
# the key in openai-secret, and which bucket credentials to publish with. Carry them over from an
# existing instance file so a cluster rebuild reproduces them instead of silently dropping them.
S3_JSON="null"; LLM_JSON="null"
if [ -n "$COPY_FROM" ]; then
    [ -f "$COPY_FROM" ] || { echo "--copy-from: no such file: $COPY_FROM" >&2; exit 1; }
    # Values move through jq only; nothing is echoed.
    S3_JSON="$(jq -c --arg p "$S3_PREFIX" '(.s3 // null) | if . then .prefix = $p else . end' "$COPY_FROM")"
    LLM_JSON="$(jq -c '.workload_llm // null' "$COPY_FROM")"
    [ "$S3_JSON"  = null ] && warn "--copy-from has no .s3 — artifacts will not be published"
    [ "$LLM_JSON" = null ] && warn "--copy-from has no .workload_llm — runs will use the benchmark default model"
fi
if [ -n "$WORKLOAD_LLM_BASE" ] || [ -n "$WORKLOAD_LLM_MODEL" ]; then
    LLM_JSON="$(jq -cn --argjson cur "$LLM_JSON" --arg b "$WORKLOAD_LLM_BASE" --arg m "$WORKLOAD_LLM_MODEL" \
        '($cur // {}) | (if $b != "" then .api_base = $b else . end)
                      | (if $m != "" then .default_model = $m else . end)')"
fi
if [ "$LLM_JSON" = null ]; then
    warn "no workload_llm (pass --copy-from, or --llm-base/--llm-model): the agent will use the"
    warn "  benchmark's baked-in default model, which the cluster's key may not be able to reach"
fi

# --- 3. write instances/<encoded-iss-host>.json ---
# Filename is keyed by the iss HOST; ':' (from :8080) is not filesystem-safe, so
# encode it to '_'. The exact iss is stored INSIDE and is the source of truth.
ISS_HOST="${ISS#*://}"; ISS_HOST="${ISS_HOST%%/*}"
ENCODED_HOST="${ISS_HOST//:/_}"
mkdir -p "$OUT_DIR"
OUT_FILE="${OUT_DIR%/}/${ENCODED_HOST}.json"

# The Service credential is the `benchmarker` identity the Service logs in as
# (per-request ROPC) to call Rossoctl — a per-instance deploy-time default,
# overridable at runtime via the benchmarker config API. iss is stored for
# identity matching only; the login/JWKS URLs are taken from the Keycloak
# BACKCHANNEL URL (keycloak_backchannel_url), NOT composed from iss — in kind the
# iss host resolves to pod loopback in-cluster and is unreachable.
# The no-auth reader still needs a bearer_token in the config, and that is not a contradiction:
# the Service mints a token BEFORE it reads, and with no bearer and no client-credentials
# `mlflow_token()` raises, which routes/runs.py catches and fails soft — an empty token report,
# identical in appearance to a broken collector. So a placeholder is emitted, which the reader
# ignores. When --mlflow-url points at an authenticating MLflow instead, the mlflow-oauth-secret
# client-credentials are emitted and no bearer, so the grant runs.
case "$MLFLOW_URL" in
    *mlflow-reader*) MLFLOW_NO_AUTH=1 ;;
    *)               MLFLOW_NO_AUTH=0 ;;
esac

# Every value reaches jq through the ENVIRONMENT, not through --arg: argv is world-readable
# (`ps`, /proc/<pid>/cmdline) and this file carries the ROPC password and the bucket keys. The
# environment of a process is readable by its own user only. Nothing here is ever echoed.
# The file is created 600 by umask, not chmod'd after the fact — there is no window in which the
# credentials sit world-readable on disk.
export ISS KC_BACKCHANNEL_URL ROSSOCTL_BASE_URL \
       KC_SERVICE_CLIENT_ID KC_SERVICE_CLIENT_SECRET KC_SERVICE_USERNAME KC_SERVICE_PASSWORD \
       MLFLOW_URL MLFLOW_CLIENT_ID MLFLOW_CLIENT_SECRET MLFLOW_TOKEN_URL MLFLOW_EXPERIMENT_ID \
       MLFLOW_NO_AUTH S3_JSON LLM_JSON WORKLOAD_OTEL_ENDPOINT WORKLOAD_AGENT_RUNNER
(
umask 077
jq -n '
    (env.S3_JSON  // "null" | fromjson) as $s3  |
    (env.LLM_JSON // "null" | fromjson) as $llm |
    {
        iss: env.ISS,
        keycloak_backchannel_url: env.KC_BACKCHANNEL_URL,
        rossoctl_base_url: env.ROSSOCTL_BASE_URL,
        service_credential: {
            client_id: env.KC_SERVICE_CLIENT_ID,
            client_secret: env.KC_SERVICE_CLIENT_SECRET,
            username: env.KC_SERVICE_USERNAME,
            password: env.KC_SERVICE_PASSWORD
        },
        mlflow: (
            { tracking_url: env.MLFLOW_URL, experiment_id: env.MLFLOW_EXPERIMENT_ID }
            + (if env.MLFLOW_NO_AUTH == "1"
               then { bearer_token: "unused-no-auth-reader" }
               else { client_id: env.MLFLOW_CLIENT_ID,
                      client_secret: env.MLFLOW_CLIENT_SECRET,
                      token_url: env.MLFLOW_TOKEN_URL } end)
        ),
        workload_otel: {
            enabled: true,
            endpoint: env.WORKLOAD_OTEL_ENDPOINT,
            protocol: "http/protobuf",
            insecure: true
        },
        workload_agent_runner: env.WORKLOAD_AGENT_RUNNER
    }
    + (if $s3  then { s3: $s3 }            else {} end)
    + (if $llm then { workload_llm: $llm } else {} end)' > "$OUT_FILE"
)
chmod 600 "$OUT_FILE"
log "==> Wrote ${OUT_FILE}"

# --- 4. next steps: mount into the in-cluster Service pod ---
# The Service is deployed IN-CLUSTER for kind (see SERVICE_DESIGN_DECISIONS.md).
# Mount the generated file as a Secret and point SERVICE_INSTANCES_DIR at it.
# The Service dials Keycloak (JWKS + ROPC) via keycloak_backchannel_url and
# Rossoctl via rossoctl_base_url — both service-DNS names resolvable in-cluster.
# iss (a *.localtest.me host) is used only as an identity string to match, never
# dialed: in-cluster it resolves to pod loopback (there is NO CoreDNS rewrite).
log ""
log "==> Next: create a Secret from ${OUT_FILE} and mount it into the Service pod, e.g.:"
cat <<EOF
kubectl --context ${KUBE_CONTEXT} create secret generic autobench-instances \\
  --from-file=$(basename "$OUT_FILE")=${OUT_FILE}
# then in the Service Deployment: mount the secret at /etc/service/instances and
# set SERVICE_INSTANCES_DIR=/etc/service/instances
EOF

log ""
log "Done."
