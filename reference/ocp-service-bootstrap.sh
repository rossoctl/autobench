#!/usr/bin/env bash
# Generate the per-instance config bundle for an OpenShift deployment of the AutoBench Service,
# and PRECHECK the cluster settings it depends on. The OpenShift sibling of
# kind-service-bootstrap.sh; same contract (writes instances/<encoded-iss-host>.json, deploys
# nothing), but the values differ per platform in ways that fail silently if copied across:
#
#   setting          kind                                    OpenShift (ykt2/ykt3/ykt5)
#   ---------------- --------------------------------------- ------------------------------------
#   LLM api_base     whatever LLM_PROFILE declares            whatever LLM_PROFILE declares
#   openai-secret    a key from THAT service's own table      a key from THAT service's own table
#   MLflow read      mlflow-reader:5000, no auth              mlflow…:8443 + SA bearer, insecure_tls
#   MLflow experiment 0                                       1, workspace team1
#   keycloak dial    backchannel svc DNS (iss unreachable)    the iss route itself
#
# No two LLM services share a key table, so a key moved between clusters does not fail
# closed at deploy — it 401s per completion, mid-run, and the leg finishes with zeroes. Neither
# does an experiment-id mismatch: the run passes and every token count reads 0, because the
# Service reads an experiment nothing was written to. Hence the precheck below, which asserts
# each of these against the live cluster before the file is written.
#
# Uses curl + jq + kubectl/oc only. Safe to re-run. No secret is ever echoed — credentials are
# read from the environment or from the cluster and only ever hashed for display.
set -euo pipefail
set +x

usage() {
    cat <<'EOF'
Usage: ocp-service-bootstrap.sh --cluster NAME [flags]

Generates instances/<iss-host>.json for an OpenShift Rossoctl instance and prechecks the cluster
settings it references. Mount the result into the Service pod as the `autobench-instances` Secret.

Required:
  --cluster NAME        cluster short name, e.g. ykt5 — used to build the apps/keycloak hosts
                        unless --apps-domain / --keycloak-host override them

Flags (each also has an env fallback):
  --context NAME        KUBE_CONTEXT       kubectl context (default: current)
  --apps-domain D       APPS_DOMAIN        router domain (default: apps.<cluster>.hcp.res.ibm.com)
  --keycloak-host H     KC_HOST            (default: keycloak-keycloak.<apps-domain>)
  --realm NAME          KC_REALM           (default: rossoctl)
  --client ID           KC_SERVICE_CLIENT_ID  (default: rossoctl)
  --rossoctl-url URL    ROSSOCTL_BASE_URL  (default: https://rossoctl-api-rossoctl-system.<apps-domain>)
  --mlflow-url URL      MLFLOW_URL         (default: https://mlflow.redhat-ods-applications.svc.cluster.local:8443)
  --mlflow-experiment I MLFLOW_EXPERIMENT_ID  (default: read from the collector's export headers)
  --mlflow-workspace W  MLFLOW_WORKSPACE      (default: read from the collector's export headers)
  --mlflow-token-secret S  MLFLOW_TOKEN_SECRET  ServiceAccount-token Secret holding the read
                        bearer, in rossoctl-system (default: mlflow-reader-token)
  --llm-profile P       LLM_PROFILE        intranet|internet — selects the INTRANET_LLM_* or
                        INTERNET_LLM_* variable set (see llm-profiles.sh). An OpenShift cluster on
                        the organisation's intranet uses `intranet`, same as a local kind cluster:
                        the gateway follows the NETWORK, not the platform.
  --llm-api-base URL    WORKLOAD_LLM_API_BASE   gateway origin; overrides the profile's. There is no
                        default and none is baked in (this repo is public)
  --llm-model M         WORKLOAD_LLM_MODEL      (default: openai/Azure/gpt-5-mini-2025-08-07)
  --otel-endpoint URL   WORKLOAD_OTEL_ENDPOINT  (default: the in-cluster collector on :8335)
  --otel-insecure B     WORKLOAD_OTEL_INSECURE  true|false (default: true for an in-cluster http URL)
  --agent-runner R      WORKLOAD_AGENT_RUNNER   (default: direct)
  --endpoint-template T ENDPOINT_TEMPLATE  set ONLY when the workloads live on another cluster,
                        e.g. 'https://{service}-{namespace}.apps.ykt2.example.com'. Leave
                        unset when Service and workloads share the cluster (ykt5) — the Service
                        then dials svc.cluster.local.
  --username U          KC_SERVICE_USERNAME  the user the Service logs in as (default: benchmarker)
  --password-file F     the benchmarker password, from a chmod-600 file. PREFERRED: the value never
                        reaches argv, so it is not visible to `ps` and not left in shell history.
  --password-stdin      read the password from stdin (for a pipeline or a secret manager)
  --password P          the password on the command line. Accepted, but argv is world-readable —
                        use --password-file unless you are in a throwaway shell.
  --s3-prefix P         S3_PREFIX          (default: <cluster>/)
  --s3-from FILE        S3_FROM            existing instance JSON to copy s3 credentials from,
                        read in-process (default: the last <out-dir>/*.json in glob order that has
                        s3 creds — so a non-default --out-dir usually finds none)
  --out-dir DIR         OUT_DIR            (default: ./instances)
  --skip-precheck       SKIP_PRECHECK=1    write the file even if a check fails
  -h, --help

Secrets:
  KC_SERVICE_USERNAME   login username (required; --username overrides)
  KC_SERVICE_PASSWORD   login password (required unless --password-file/-stdin/--password is given;
                        those take precedence, in that order)
  KC_SERVICE_CLIENT_SECRET  client secret, if confidential (optional)
  S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY  override --s3-from

The password is load-bearing per RUN, not just per install: every /deploy the Service makes is a
ROPC login as this user, so a stale or unset one does not degrade the install — it makes every
benchmark fail with a 502 wrapping a 403. The precheck below therefore logs in for real.
EOF
}

log()  { printf '%s\n' "$*" >&2; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'Error: %s\n' "$*" >&2; exit 1; }
sha8() { printf '%s' "$1" | shasum -a 256 | cut -c1-8; }

command -v curl >/dev/null || die "curl is required"
command -v jq   >/dev/null || die "jq is required"

CLUSTER="${CLUSTER:-}"
KUBE_CONTEXT="${KUBE_CONTEXT:-}"
APPS_DOMAIN="${APPS_DOMAIN:-}"
KC_HOST="${KC_HOST:-}"
KC_REALM="${KC_REALM:-rossoctl}"
KC_SERVICE_CLIENT_ID="${KC_SERVICE_CLIENT_ID:-rossoctl}"
ROSSOCTL_BASE_URL="${ROSSOCTL_BASE_URL:-}"
MLFLOW_URL="${MLFLOW_URL:-https://mlflow.redhat-ods-applications.svc.cluster.local:8443}"
MLFLOW_EXPERIMENT_ID="${MLFLOW_EXPERIMENT_ID:-}"
MLFLOW_WORKSPACE="${MLFLOW_WORKSPACE:-}"
MLFLOW_TOKEN_SECRET="${MLFLOW_TOKEN_SECRET:-mlflow-reader-token}"
WORKLOAD_LLM_API_BASE="${WORKLOAD_LLM_API_BASE:-}"
WORKLOAD_LLM_MODEL="${WORKLOAD_LLM_MODEL:-openai/Azure/gpt-5-mini-2025-08-07}"
WORKLOAD_OTEL_ENDPOINT="${WORKLOAD_OTEL_ENDPOINT:-http://otel-collector.rossoctl-system.svc.cluster.local:8335}"
WORKLOAD_OTEL_INSECURE="${WORKLOAD_OTEL_INSECURE:-}"
WORKLOAD_AGENT_RUNNER="${WORKLOAD_AGENT_RUNNER:-direct}"
ENDPOINT_TEMPLATE="${ENDPOINT_TEMPLATE:-}"
S3_PREFIX="${S3_PREFIX:-}"
S3_FROM="${S3_FROM:-}"
OUT_DIR="${OUT_DIR:-./instances}"
SKIP_PRECHECK="${SKIP_PRECHECK:-}"
KUBECTL_BIN="${KUBECTL_BIN:-kubectl}"
KC_SERVICE_USERNAME="${KC_SERVICE_USERNAME:-benchmarker}"
CRED_PASSWORD_FILE=""
CRED_PASSWORD_STDIN=""
CRED_PASSWORD_ARGV=""

while [ $# -gt 0 ]; do
    case "$1" in
        --cluster)            CLUSTER="$2"; shift 2 ;;
        --context)            KUBE_CONTEXT="$2"; shift 2 ;;
        --apps-domain)        APPS_DOMAIN="$2"; shift 2 ;;
        --keycloak-host)      KC_HOST="$2"; shift 2 ;;
        --realm)              KC_REALM="$2"; shift 2 ;;
        --client)             KC_SERVICE_CLIENT_ID="$2"; shift 2 ;;
        --rossoctl-url)       ROSSOCTL_BASE_URL="$2"; shift 2 ;;
        --mlflow-url)         MLFLOW_URL="$2"; shift 2 ;;
        --mlflow-experiment)  MLFLOW_EXPERIMENT_ID="$2"; shift 2 ;;
        --mlflow-workspace)   MLFLOW_WORKSPACE="$2"; shift 2 ;;
        --mlflow-token-secret) MLFLOW_TOKEN_SECRET="$2"; shift 2 ;;
        --llm-profile)        LLM_PROFILE="$2"; shift 2 ;;
        --llm-api-base)       WORKLOAD_LLM_API_BASE="$2"; shift 2 ;;
        --llm-model)          WORKLOAD_LLM_MODEL="$2"; shift 2 ;;
        --otel-endpoint)      WORKLOAD_OTEL_ENDPOINT="$2"; shift 2 ;;
        --otel-insecure)      WORKLOAD_OTEL_INSECURE="$2"; shift 2 ;;
        --agent-runner)       WORKLOAD_AGENT_RUNNER="$2"; shift 2 ;;
        --endpoint-template)  ENDPOINT_TEMPLATE="$2"; shift 2 ;;
        --username)           KC_SERVICE_USERNAME="$2"; shift 2 ;;
        --password-file)      CRED_PASSWORD_FILE="$2"; shift 2 ;;
        --password-stdin)     CRED_PASSWORD_STDIN=1; shift ;;
        --password)           CRED_PASSWORD_ARGV="$2"; shift 2 ;;
        --s3-prefix)          S3_PREFIX="$2"; shift 2 ;;
        --s3-from)            S3_FROM="$2"; shift 2 ;;
        --out-dir)            OUT_DIR="$2"; shift 2 ;;
        --skip-precheck)      SKIP_PRECHECK=1; shift ;;
        -h|--help)            usage; exit 0 ;;
        *)                    usage; die "unknown argument '$1'" ;;
    esac
done

[ -n "$CLUSTER" ] || { usage; die "--cluster is required"; }
[ -n "${KC_SERVICE_USERNAME:-}" ] || die "--username (or KC_SERVICE_USERNAME) must not be empty"

# shellcheck source=reference/credfile.sh
. "$(dirname "${BASH_SOURCE[0]}")/credfile.sh"
n=0
[ -n "$CRED_PASSWORD_FILE" ]  && n=$((n+1))
[ -n "$CRED_PASSWORD_STDIN" ] && n=$((n+1))
[ -n "$CRED_PASSWORD_ARGV" ]  && n=$((n+1))
[ "$n" -le 1 ] || die "--password-file, --password-stdin and --password are mutually exclusive"
rc=0; cred_resolve_password KC_SERVICE_PASSWORD || rc=$?   # `; rc=$?` would trip set -e first
case "$rc" in
    0) ;;
    2) die "no password for ${KC_SERVICE_USERNAME}: pass --password-file <chmod-600 file>, --password-stdin, --password, or set KC_SERVICE_PASSWORD" ;;
    *) exit 1 ;;   # cred_read_file already said why
esac
# shellcheck source=reference/llm-profiles.sh
. "$(dirname "${BASH_SOURCE[0]}")/llm-profiles.sh"
llm_profile_resolve || die "could not resolve LLM_PROFILE=${LLM_PROFILE:-}"
[ -n "$WORKLOAD_LLM_API_BASE" ] || die "a gateway is required: --llm-profile intranet|internet, or --llm-api-base"
command -v "$KUBECTL_BIN" >/dev/null || die "$KUBECTL_BIN is required"

APPS_DOMAIN="${APPS_DOMAIN:-apps.${CLUSTER}.hcp.res.ibm.com}"
KC_HOST="${KC_HOST:-keycloak-keycloak.${APPS_DOMAIN}}"
ROSSOCTL_BASE_URL="${ROSSOCTL_BASE_URL:-https://rossoctl-api-rossoctl-system.${APPS_DOMAIN}}"
S3_PREFIX="${S3_PREFIX:-${CLUSTER}/}"
if [ -z "$WORKLOAD_OTEL_INSECURE" ]; then
    case "$WORKLOAD_OTEL_ENDPOINT" in http://*) WORKLOAD_OTEL_INSECURE=true ;; *) WORKLOAD_OTEL_INSECURE=false ;; esac
fi

kc() { if [ -n "$KUBE_CONTEXT" ]; then "$KUBECTL_BIN" --context "$KUBE_CONTEXT" "$@"; else "$KUBECTL_BIN" "$@"; fi; }

# --- 1. issuer, authoritative from .well-known ---
log "==> Resolving issuer from Keycloak (${KC_HOST}, realm ${KC_REALM})..."
ISS="$(curl -sS "https://${KC_HOST}/realms/${KC_REALM}/.well-known/openid-configuration" \
        | jq -r '.issuer // empty' 2>/dev/null || true)"
if [ -z "$ISS" ]; then
    ISS="https://${KC_HOST}/realms/${KC_REALM}"
    warn "could not fetch .well-known; using constructed iss ${ISS}"
else
    log "    iss = ${ISS}"
fi
# Unlike kind, the iss route IS reachable from inside an OpenShift cluster, so no backchannel
# override is needed: the Service composes the JWKS and ROPC URLs from iss.

# --- 2. MLflow read bearer, from the ServiceAccount-token Secret ---
log "==> Reading MLflow read bearer from secret ${MLFLOW_TOKEN_SECRET} (rossoctl-system)..."
MLFLOW_BEARER="$(kc -n rossoctl-system get secret "$MLFLOW_TOKEN_SECRET" \
    -o jsonpath='{.data.token}' 2>/dev/null | base64 -d 2>/dev/null || true)"
if [ -n "$MLFLOW_BEARER" ]; then
    log "    bearer present (len ${#MLFLOW_BEARER}, sha8 $(sha8 "$MLFLOW_BEARER"))"
else
    warn "secret ${MLFLOW_TOKEN_SECRET} has no .data.token — MLflow reads will fail, so every"
    warn "         per-task token count will read 0 while the run still passes. Create it with:"
    warn "           oc -n rossoctl-system create sa mlflow-reader"
    warn "           oc -n rossoctl-system apply -f - <<'Y'"
    warn "           apiVersion: v1"
    warn "           kind: Secret"
    warn "           metadata:"
    warn "             name: ${MLFLOW_TOKEN_SECRET}"
    warn "             annotations: {kubernetes.io/service-account.name: mlflow-reader}"
    warn "           type: kubernetes.io/service-account-token"
    warn "           Y"
fi

# --- 3. MLflow experiment/workspace, defaulted from what the collector actually writes ---
# Reading them from the collector's export headers rather than assuming: the Service must read the
# experiment the collector WRITES, and a mismatch is invisible (pass, zero tokens).
COLLECTOR_CFG="$(kc -n rossoctl-system get cm otel-collector-config -o jsonpath='{.data.base\.yaml}' 2>/dev/null || true)"
if [ -n "$COLLECTOR_CFG" ]; then
    [ -n "$MLFLOW_EXPERIMENT_ID" ] || MLFLOW_EXPERIMENT_ID="$(printf '%s' "$COLLECTOR_CFG" \
        | sed -n 's/.*x-mlflow-experiment-id: *"\{0,1\}\([^"]*\)"\{0,1\}.*/\1/p' | head -1)"
    [ -n "$MLFLOW_WORKSPACE" ] || MLFLOW_WORKSPACE="$(printf '%s' "$COLLECTOR_CFG" \
        | sed -n 's/.*x-mlflow-workspace: *"\{0,1\}\([^" ]*\)"\{0,1\}.*/\1/p' | head -1)"
fi
MLFLOW_EXPERIMENT_ID="${MLFLOW_EXPERIMENT_ID:-1}"
MLFLOW_WORKSPACE="${MLFLOW_WORKSPACE:-team1}"
log "    MLflow experiment_id=${MLFLOW_EXPERIMENT_ID} workspace=${MLFLOW_WORKSPACE}"

# --- 4. S3 credentials, copied in-process from an existing instance file ---
if [ -z "${S3_ACCESS_KEY_ID:-}" ] || [ -z "${S3_SECRET_ACCESS_KEY:-}" ]; then
    if [ -z "$S3_FROM" ]; then
        for f in "${OUT_DIR%/}"/*.json; do
            [ -f "$f" ] || continue
            if [ "$(jq -r '.s3.access_key_id // empty' "$f" 2>/dev/null)" != "" ]; then S3_FROM="$f"; fi
        done
    fi
    if [ -n "$S3_FROM" ] && [ -f "$S3_FROM" ]; then
        S3_ACCESS_KEY_ID="$(jq -r '.s3.access_key_id // empty' "$S3_FROM")"
        S3_SECRET_ACCESS_KEY="$(jq -r '.s3.secret_access_key // empty' "$S3_FROM")"
        S3_BUCKET="${S3_BUCKET:-$(jq -r '.s3.bucket // empty' "$S3_FROM")}"
        S3_REGION="${S3_REGION:-$(jq -r '.s3.region // empty' "$S3_FROM")}"
        log "==> S3 credentials copied from ${S3_FROM} (sha8 $(sha8 "${S3_ACCESS_KEY_ID:-}"))"
    else
        warn "no S3 credentials found — the run will score but publish no artifacts"
    fi
fi
S3_BUCKET="${S3_BUCKET:-rossoctl-benchmarking}"
S3_REGION="${S3_REGION:-us-east-1}"

# --- 5. precheck the settings against the live cluster ---
# Each of these failing produces a run that looks fine and reports nothing useful, which is why
# they are asserted here rather than discovered during a 12-run.
PRECHECK_FAIL=0
check() {  # $1=label $2=ok(0/1) $3=detail
    if [ "$2" = "0" ]; then printf 'ok    %s\n' "$1" >&2
    else printf 'FAIL  %s — %s\n' "$1" "$3" >&2; PRECHECK_FAIL=$((PRECHECK_FAIL+1)); fi
}
log ""
log "==> Prechecking cluster settings (${CLUSTER})"

# Rossoctl v0.8.0 or later is a hard prerequisite: the Service's deploy request carries fields the
# older operator contract silently drops (k8sResourceLimits among them), so an older cluster
# accepts the deploy and runs a differently-shaped workload. The platform version is the BACKEND's
# chart label — the operator subchart carries its own, lower, version line (0.4.x) and comparing
# that one would reject a perfectly current cluster.
ROSSOCTL_VER="$(kc -n rossoctl-system get deploy rossoctl-backend \
    -o jsonpath='{.metadata.labels.app\.kubernetes\.io/version}' 2>/dev/null || true)"
ver_ok() {  # $1=found — true when >= 0.8.0, treating 0.8.0-rc.N as 0.8.0
    local v="${1%%-*}" major minor
    major="${v%%.*}"; minor="${v#*.}"; minor="${minor%%.*}"
    case "$major$minor" in ''|*[!0-9]*) return 1 ;; esac
    [ "$major" -gt 0 ] || [ "$minor" -ge 8 ]
}
if [ -z "$ROSSOCTL_VER" ]; then
    check "Rossoctl >= 0.8.0" 1 "could not read app.kubernetes.io/version from deploy/rossoctl-backend"
elif ver_ok "$ROSSOCTL_VER"; then
    check "Rossoctl >= 0.8.0 (found ${ROSSOCTL_VER})" 0
else
    check "Rossoctl >= 0.8.0" 1 "found ${ROSSOCTL_VER} — upgrade the cluster before deploying AutoBench"
fi

# Where the password came from, so a green precheck says WHICH credential it proved. The value is
# hashed, never shown; the hash is also what lets you compare it against the instance file's copy
# (reference/preflight.py does that comparison) without either being printed.
check "${KC_SERVICE_USERNAME} password from ${CRED_PASSWORD_SOURCE} (sha8 $(sha8 "$KC_SERVICE_PASSWORD"))" 0
[ "$CRED_PASSWORD_SOURCE" = "--password (argv)" ] && \
    warn "argv is world-readable; prefer --password-file for anything but a throwaway shell"

# ROPC actually works for this credential, against this iss. The failure is CLASSIFIED rather than
# lumped: "wrong password", "no password credential", "profile incomplete" and "client not
# direct-access" need four different fixes, and Keycloak's own wording separates only some of them.
ROPC_BODY="$(curl -sS "${ISS}/protocol/openid-connect/token" \
        -o /dev/stdout -w '\n%{http_code}' \
        -d client_id="$KC_SERVICE_CLIENT_ID" \
        ${KC_SERVICE_CLIENT_SECRET:+-d client_secret="$KC_SERVICE_CLIENT_SECRET"} \
        -d grant_type=password -d username="$KC_SERVICE_USERNAME" \
        --data-urlencode "password=${KC_SERVICE_PASSWORD}" 2>/dev/null || true)"
ROPC_STATUS="${ROPC_BODY##*$'\n'}"
ROPC_BODY="${ROPC_BODY%$'\n'*}"
TOK="$(printf '%s' "$ROPC_BODY" | jq -r '.access_token // empty' 2>/dev/null || true)"
if [ -n "$TOK" ]; then
    check "ROPC login as ${KC_SERVICE_USERNAME}" 0
else
    check "ROPC login as ${KC_SERVICE_USERNAME}" 1 "$(cred_ropc_cause "$ROPC_STATUS" "$ROPC_BODY")"
fi

# The realm role every /deploy needs. Without it the Service surfaces a 403 as a 502.
if [ -n "$TOK" ]; then
    ROLES="$(cred_jwt_realm_roles "$TOK")"   # padded: `base64 -d` on a raw JWT segment truncates
    case ",$ROLES," in *,rossoctl-operator,*) check "realm role rossoctl-operator" 0 ;;
        *) check "realm role rossoctl-operator" 1 "not in the token's realm_access.roles" ;; esac
fi

# The workload LLM key: present and non-empty. Only the hash is shown. WHICH service issued it is
# not assertable from here — that is what the profile check below is for — and an empty apikey is
# the common post-reinstall state, which 401s every completion.
#
# Only the namespaces actually deployed into, which is why TEAM_NAMESPACES is `team1` and not
# `team1 team2`: all 24 specs in reference/run12_specs.json name team1, and failing the precheck on
# an unused namespace stops a correct install for nothing. Override when you deploy elsewhere.
TEAM_NAMESPACES="${TEAM_NAMESPACES:-team1}"
for ns in $TEAM_NAMESPACES; do
    K="$(kc -n "$ns" get secret openai-secret -o jsonpath='{.data.apikey}' 2>/dev/null | base64 -d 2>/dev/null || true)"
    if [ -z "$K" ]; then check "openai-secret apikey in ${ns}" 1 "absent or empty — every completion will 401"
    else check "openai-secret apikey in ${ns} (sha8 $(sha8 "$K"))" 0; fi
done
for ns in $TEAM_NAMESPACES; do
    kc -n "$ns" get secret hf-secret >/dev/null 2>&1 \
        && check "hf-secret in ${ns}" 0 \
        || check "hf-secret in ${ns}" 1 "absent — the MCP pod stays in CreateContainerConfigError"
done

# The gateway this config points the workloads at must be the one whose key table issued the key in
# openai-secret. Which gateway that is depends on where this cluster sits on the network — an
# OpenShift cluster on the intranet uses the internal one — so the only thing assertable here is
# that the base agrees with the DECLARED profile. With no profile declared there is nothing to
# compare against and the check is skipped rather than guessed: the previous version of this check
# rejected the internal gateway outright, which is wrong for an intranet OpenShift cluster.
if [ -n "${LLM_PROFILE:-}" ]; then
    if llm_profile_base_matches "$WORKLOAD_LLM_API_BASE"; then
        check "LLM gateway matches the ${LLM_PROFILE} profile" 0
    else
        check "LLM gateway matches the ${LLM_PROFILE} profile" 1 \
            "--llm-api-base is not the ${LLM_PROFILE} profile's base; no two LLM services share a key table, so this 401s per completion mid-run"
    fi
else
    warn "no --llm-profile: the base cannot be checked against the gateway that issued the key"
fi

# The collector's HTTP receiver. The shipped ConfigMap says 4318, but the Deployment overrides it
# on the command line (`--set receivers::otlp::protocols::http::endpoint=0.0.0.0:8335`), so 8335 is
# the ONLY http port that listens. Pointing the agent at 4318 hard-fails it into CrashLoopBackOff,
# which surfaces as a 424 on the run.
OTEL_PORT="${WORKLOAD_OTEL_ENDPOINT##*:}"; OTEL_PORT="${OTEL_PORT%%/*}"
COLLECTOR_CMD="$(kc -n rossoctl-system get deploy otel-collector \
    -o jsonpath='{.spec.template.spec.containers[0].command}' 2>/dev/null || true)"
case "$COLLECTOR_CMD" in
    *"0.0.0.0:${OTEL_PORT}"*) check "collector http receiver on :${OTEL_PORT}" 0 ;;
    "") check "collector http receiver on :${OTEL_PORT}" 1 "otel-collector Deployment not found in rossoctl-system" ;;
    *)  check "collector http receiver on :${OTEL_PORT}" 1 \
            "the collector's command does not bind :${OTEL_PORT} (it overrides the ConfigMap's port)" ;;
esac

# MLflow must be where the config says, and must be the same instance the collector writes to.
MLFLOW_HOSTPORT="${MLFLOW_URL#*://}"; MLFLOW_HOSTPORT="${MLFLOW_HOSTPORT%%/*}"
MLFLOW_SVC="${MLFLOW_HOSTPORT%%.*}"; MLFLOW_NS="${MLFLOW_HOSTPORT#*.}"; MLFLOW_NS="${MLFLOW_NS%%.*}"
kc -n "$MLFLOW_NS" get svc "$MLFLOW_SVC" >/dev/null 2>&1 \
    && check "MLflow svc ${MLFLOW_SVC} in ${MLFLOW_NS}" 0 \
    || check "MLflow svc ${MLFLOW_SVC} in ${MLFLOW_NS}" 1 "not found — check --mlflow-url"
case "$COLLECTOR_CFG" in
    *"$MLFLOW_HOSTPORT"*) check "collector writes to the MLflow the Service reads" 0 ;;
    *) check "collector writes to the MLflow the Service reads" 1 \
            "the collector's traces_endpoint does not contain ${MLFLOW_HOSTPORT}" ;;
esac

if [ "$PRECHECK_FAIL" -ne 0 ] && [ -z "$SKIP_PRECHECK" ]; then
    log ""
    die "${PRECHECK_FAIL} precheck failure(s) — fix the cluster, or pass --skip-precheck to write anyway"
fi

# --- 6. write instances/<encoded-iss-host>.json ---
ISS_HOST="${ISS#*://}"; ISS_HOST="${ISS_HOST%%/*}"
mkdir -p "$OUT_DIR"
OUT_FILE="${OUT_DIR%/}/${ISS_HOST//:/_}.json"

NO_PROXY_HOSTS="127.0.0.1,localhost,${WORKLOAD_LLM_API_BASE#*://},otel-collector.rossoctl-system.svc.cluster.local,keycloak.keycloak.svc.cluster.local"

jq -n \
    --arg iss "$ISS" \
    --arg rossoctl "$ROSSOCTL_BASE_URL" \
    --arg scid "$KC_SERVICE_CLIENT_ID" \
    --arg scsec "${KC_SERVICE_CLIENT_SECRET:-}" \
    --arg suser "$KC_SERVICE_USERNAME" \
    --arg spass "$KC_SERVICE_PASSWORD" \
    --arg murl "$MLFLOW_URL" \
    --arg mtok "${MLFLOW_BEARER:-}" \
    --arg mexp "$MLFLOW_EXPERIMENT_ID" \
    --arg mws "$MLFLOW_WORKSPACE" \
    --arg sbucket "$S3_BUCKET" \
    --arg sregion "$S3_REGION" \
    --arg sak "${S3_ACCESS_KEY_ID:-}" \
    --arg ssk "${S3_SECRET_ACCESS_KEY:-}" \
    --arg sprefix "$S3_PREFIX" \
    --arg tmpl "$ENDPOINT_TEMPLATE" \
    --arg lbase "$WORKLOAD_LLM_API_BASE" \
    --arg lmodel "$WORKLOAD_LLM_MODEL" \
    --arg lnoproxy "$NO_PROXY_HOSTS" \
    --arg oep "$WORKLOAD_OTEL_ENDPOINT" \
    --argjson oinsecure "$WORKLOAD_OTEL_INSECURE" \
    --arg runner "$WORKLOAD_AGENT_RUNNER" \
    '{
        iss: $iss,
        rossoctl_base_url: $rossoctl,
        service_credential: { client_id: $scid, client_secret: $scsec, username: $suser, password: $spass },
        mlflow: { tracking_url: $murl, bearer_token: $mtok, experiment_id: $mexp, workspace: $mws, insecure_tls: true },
        s3: { bucket: $sbucket, region: $sregion, access_key_id: $sak, secret_access_key: $ssk, prefix: $sprefix },
        workload_llm: { api_base: $lbase, default_model: $lmodel, no_proxy: $lnoproxy },
        workload_otel: { enabled: true, endpoint: $oep, protocol: "http/protobuf", insecure: $oinsecure },
        workload_agent_runner: $runner
      }
      # Only set for a cross-cluster split (Service here, workloads there); when unset the Service
      # dials the co-located svc.cluster.local address.
      + (if $tmpl == "" then {} else { mcp_endpoint_template: $tmpl, agent_endpoint_template: $tmpl } end)' \
    > "$OUT_FILE"
chmod 600 "$OUT_FILE"
log ""
log "==> Wrote ${OUT_FILE}"

log ""
log "==> Next: create the Secret the chart mounts, then install the chart:"
cat <<EOF
oc${KUBE_CONTEXT:+ --context $KUBE_CONTEXT} -n rossoctl-system create secret generic autobench-instances \\
  --from-file=$(basename "$OUT_FILE")=${OUT_FILE} --dry-run=client -o yaml | oc apply -f -

helm upgrade --install autobench deploy/helm/autobench -n rossoctl-system \\
  -f deploy/helm/values-openshift.yaml
EOF
log ""
log "Done."
