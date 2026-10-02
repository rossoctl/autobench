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
# Uses curl + jq + kubectl/oc + uv (the repo's Python, for the S3 check). Safe to re-run. No secret is ever echoed — credentials are
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
  S3_ENABLED=true|false in the environment or an --env-file — whether runs publish artifacts.
                        No default, see "S3" below.

  --env-file FILE       KEY=VALUE lines (chmod 600), repeatable; see reference/autobench.env.template.
                        Precedence, last wins: the shell, then each --env-file in order, then flags.

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
  --mlflow-insecure-tls B  MLFLOW_INSECURE_TLS  true|false (default: true — the in-cluster RHOAI
                        endpoint is reencrypt with a service-CA cert. Set false for an MLflow
                        behind a certificate this pod's trust store already accepts)

MLflow credentials (pick ONE shape; they are the four the Service itself resolves, in this
precedence). An MLflow that already exists on the cluster is authenticated by whoever installed it,
not by us, so its credential has to be supplied rather than discovered:
  --mlflow-bearer-file F   a chmod-600 file holding a pre-obtained bearer, read in-process
  --mlflow-username U      with --mlflow-password-file F, and optionally --mlflow-oauth-url URL:
                           the OpenShift OAuth challenge flow an RHOAI oauth-proxy expects (the
                           oauth URL is derived from the tracking URL when omitted)
  --mlflow-client-id ID    with --mlflow-client-secret-file F and --mlflow-token-url URL: an OIDC
                           client-credentials grant, for an MLflow fronted by Keycloak
  --mlflow-no-auth         declare this MLflow unauthenticated. What kind's mlflow-reader is, and
                           the ONLY way to install with no MLflow credential — otherwise a resolved
                           credential of none is a precheck FAILURE, because every run would then
                           pass and publish an empty token report.
  (no flag)                fall back to --mlflow-token-secret, the SA-token Secret shape

Half of a shape is an error, not a fallback: --mlflow-username without --mlflow-password-file stops
here rather than quietly reverting to the Secret and installing a credential nobody chose.
  --llm-profile P       LLM_PROFILE        intranet|internet — selects the INTRANET_LLM_* or
                        INTERNET_LLM_* variable set (see llm-profiles.sh). An OpenShift cluster on
                        the organisation's intranet uses `intranet`, same as a local kind cluster:
                        the gateway follows the NETWORK, not the platform.
  --llm-api-base URL    WORKLOAD_LLM_API_BASE   gateway origin; overrides the profile's. There is no
                        default and none is baked in (this repo is public)
  --llm-model M         WORKLOAD_LLM_MODEL      a model id from the SELECTED gateway's catalogue.
                        Required, and no default: a catalogue is per-gateway and it is revised under
                        you, so the profile is the only place to write one down
  --otel-endpoint URL   WORKLOAD_OTEL_ENDPOINT  (default: the in-cluster collector on :8335)
  --otel-insecure B     WORKLOAD_OTEL_INSECURE  true|false (default: true for an in-cluster http URL)
  --agent-runner R      WORKLOAD_AGENT_RUNNER   service|direct (default: service — `direct` yields a
                        clean run with ZERO token rows on the current agent image)
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
  --out-dir DIR         OUT_DIR            (default: ./instances)
  --print-out-file                         print only the written file's path on stdout, instead of
                                           the next steps (what autobench-install.sh reads)
  --skip-precheck       SKIP_PRECHECK=1    write the file even if a check fails
  -h, --help

S3 — environment only (an --env-file is the intended carrier), never argv, never copied from a file:
  S3_ENABLED            true|false, REQUIRED. false writes no s3 block: runs score, publish nothing.
  S3_BUCKET, S3_REGION, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY
                        all required when S3_ENABLED=true; the key is proven by a signed read-only
                        request (reference/s3check.py) before the file is written
  S3_ENDPOINT_URL       optional, an S3-compatible store's http(s)://host[:port]

Secrets:
  KC_SERVICE_USERNAME   login username (required; --username overrides)
  KC_SERVICE_PASSWORD   login password (required unless --password-file/-stdin/--password is given;
                        those take precedence, in that order)
  KC_SERVICE_CLIENT_SECRET  client secret, if confidential (optional)
  S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY  see "S3" above

The password is load-bearing per RUN, not just per install: every /deploy the Service makes is a
ROPC login as this user, so a stale or unset one does not degrade the install — it makes every
benchmark fail with a 502 wrapping a 403. The precheck below therefore logs in for real.
EOF
}

log()  { printf '%s\n' "$*" >&2; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'Error: %s\n' "$*" >&2; exit 1; }
sha8() { printf '%s' "$1" | shasum -a 256 | cut -c1-8; }

command -v curl    >/dev/null || die "curl is required"
command -v jq      >/dev/null || die "jq is required"
# shellcheck source=reference/pyrun.sh
. "$(dirname "${BASH_SOURCE[0]}")/pyrun.sh"
repo_python "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || die "$REPO_PYTHON_HINT"

# Before ANY default below reads the environment: that ordering is what makes an --env-file beat the
# shell while the flag loop still beats the file.
# shellcheck source=reference/envfile.sh
. "$(dirname "${BASH_SOURCE[0]}")/envfile.sh"
envfile_prescan "$@" || exit 1

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
MLFLOW_INSECURE_TLS="${MLFLOW_INSECURE_TLS:-true}"
MLFLOW_BEARER_FILE="${MLFLOW_BEARER_FILE:-}"
MLFLOW_USERNAME="${MLFLOW_USERNAME:-}"
MLFLOW_PASSWORD_FILE="${MLFLOW_PASSWORD_FILE:-}"
MLFLOW_OAUTH_URL="${MLFLOW_OAUTH_URL:-}"
MLFLOW_CLIENT_ID="${MLFLOW_CLIENT_ID:-}"
MLFLOW_CLIENT_SECRET_FILE="${MLFLOW_CLIENT_SECRET_FILE:-}"
MLFLOW_TOKEN_URL="${MLFLOW_TOKEN_URL:-}"
MLFLOW_NO_AUTH="${MLFLOW_NO_AUTH:-}"
WORKLOAD_LLM_API_BASE="${WORKLOAD_LLM_API_BASE:-}"
# Empty, like the base above, and for the same reason: llm_profile_resolve only fills a name that is
# still unset, so ANY default here outranks the profile and makes --llm-profile inert for the model
# while still resolving the base -- a config that names one gateway's host and another's catalogue.
# It failed exactly that way on 2026-09-29. A model id is per-gateway, so there is nothing safe to
# default it to; require one instead.
WORKLOAD_LLM_MODEL="${WORKLOAD_LLM_MODEL:-}"
WORKLOAD_OTEL_ENDPOINT="${WORKLOAD_OTEL_ENDPOINT:-http://otel-collector.rossoctl-system.svc.cluster.local:8335}"
WORKLOAD_OTEL_INSECURE="${WORKLOAD_OTEL_INSECURE:-}"
# `service`, not `direct`: with the current agent image `service` is what emits agent spans and
# `direct` produces a clean-looking run whose token rows are all ZERO (docs/ADMIN_GUIDE.md §"the
# runner"). The old `direct` default came from a kind measurement on 2026-08-31 where `service` lost
# token rows at max_parallel_sessions=4 — that was the warm-agent attribution loss, fixed in agent
# dev145, and the note at src/autobench/models.py:132 records it as history. Six of the twelve legs
# run at p=4, and the canonical matrix deploys with EXGENTIC_DEFAULT_RUNNER=service (see leg #9 in
# run12_specs.json), so `direct` here contradicted the matrix it exists to set up. Do not infer which
# one is live — look for a non-zero token row in report.ndjson.
WORKLOAD_AGENT_RUNNER="${WORKLOAD_AGENT_RUNNER:-service}"
ENDPOINT_TEMPLATE="${ENDPOINT_TEMPLATE:-}"
S3_PREFIX="${S3_PREFIX:-}"
S3_ENABLED="${S3_ENABLED:-}"
PRINT_OUT_FILE=""
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
        --mlflow-insecure-tls) MLFLOW_INSECURE_TLS="$2"; shift 2 ;;
        --mlflow-bearer-file) MLFLOW_BEARER_FILE="$2"; shift 2 ;;
        --mlflow-username)    MLFLOW_USERNAME="$2"; shift 2 ;;
        --mlflow-password-file) MLFLOW_PASSWORD_FILE="$2"; shift 2 ;;
        --mlflow-oauth-url)   MLFLOW_OAUTH_URL="$2"; shift 2 ;;
        --mlflow-client-id)   MLFLOW_CLIENT_ID="$2"; shift 2 ;;
        --mlflow-client-secret-file) MLFLOW_CLIENT_SECRET_FILE="$2"; shift 2 ;;
        --mlflow-token-url)   MLFLOW_TOKEN_URL="$2"; shift 2 ;;
        --mlflow-no-auth)     MLFLOW_NO_AUTH=1; shift ;;
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
        --s3-from)            die "--s3-from was removed: S3 now comes from the environment only — put S3_ENABLED and the S3_* values in an --env-file (see reference/autobench.env.template)" ;;
        --env-file)           shift 2 ;;   # already loaded by envfile_prescan
        --env-file=*)         shift ;;
        --print-out-file)     PRINT_OUT_FILE=1; shift ;;
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
[ -n "$WORKLOAD_LLM_MODEL" ] || die "a model is required: --llm-model, or set <INTRANET|INTERNET>_LLM_MODEL for the selected profile. It must be an id THAT gateway lists in GET /v1/models — a model the key cannot see fails one completion at a time, mid-leg"
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

# --- 2. MLflow credential, in the shape the installer declared ---
# On OpenShift MLflow is PRE-INSTALLED — rossoctl-deps or RHOAI put it there, SAR-gated on :8443 —
# so its credential belongs to whoever installed it and cannot be discovered from here. The four
# shapes below are exactly the ones src/autobench/auth/mlflow.py resolves, in its precedence order,
# so that what this script writes and what the Service does with it cannot drift apart. Every value
# comes from a chmod-600 file or from the cluster, never from argv, and is displayed only as a hash.
#
# Half a shape stops the script. Falling back to the Secret because a password file was forgotten
# would install a credential nobody chose, and the resulting failure is the silent one: reads 403,
# token counts read 0, the run still passes.
MLFLOW_BEARER=""
MLFLOW_PASSWORD=""
MLFLOW_CLIENT_SECRET=""
MLFLOW_MODE="none"
if [ -n "$MLFLOW_NO_AUTH" ]; then
    MLFLOW_MODE="none (--mlflow-no-auth)"
    log "==> MLflow declared unauthenticated (--mlflow-no-auth)"
elif [ -n "$MLFLOW_BEARER_FILE" ]; then
    MLFLOW_BEARER="$(cred_read_file "$MLFLOW_BEARER_FILE")" || exit 1
    [ -n "$MLFLOW_BEARER" ] || die "${MLFLOW_BEARER_FILE} is empty"
    MLFLOW_MODE="bearer_token (file ${MLFLOW_BEARER_FILE})"
    log "==> MLflow bearer from ${MLFLOW_BEARER_FILE} (sha8 $(sha8 "$MLFLOW_BEARER"))"
elif [ -n "$MLFLOW_USERNAME" ] || [ -n "$MLFLOW_PASSWORD_FILE" ]; then
    [ -n "$MLFLOW_USERNAME" ]      || die "--mlflow-password-file needs --mlflow-username"
    [ -n "$MLFLOW_PASSWORD_FILE" ] || die "--mlflow-username needs --mlflow-password-file (a chmod-600 file; the value must not reach argv)"
    MLFLOW_PASSWORD="$(cred_read_file "$MLFLOW_PASSWORD_FILE")" || exit 1
    [ -n "$MLFLOW_PASSWORD" ] || die "${MLFLOW_PASSWORD_FILE} is empty"
    MLFLOW_MODE="openshift_oauth (user ${MLFLOW_USERNAME})"
    log "==> MLflow OpenShift OAuth as ${MLFLOW_USERNAME} (sha8 $(sha8 "$MLFLOW_PASSWORD"))"
    [ -n "$MLFLOW_OAUTH_URL" ] || log "    no --mlflow-oauth-url: the Service derives it from the tracking URL"
elif [ -n "$MLFLOW_CLIENT_ID" ] || [ -n "$MLFLOW_CLIENT_SECRET_FILE" ] || [ -n "$MLFLOW_TOKEN_URL" ]; then
    [ -n "$MLFLOW_CLIENT_ID" ]          || die "client-credentials for MLflow needs --mlflow-client-id"
    [ -n "$MLFLOW_CLIENT_SECRET_FILE" ] || die "client-credentials for MLflow needs --mlflow-client-secret-file (a chmod-600 file)"
    [ -n "$MLFLOW_TOKEN_URL" ]          || die "client-credentials for MLflow needs --mlflow-token-url"
    MLFLOW_CLIENT_SECRET="$(cred_read_file "$MLFLOW_CLIENT_SECRET_FILE")" || exit 1
    [ -n "$MLFLOW_CLIENT_SECRET" ] || die "${MLFLOW_CLIENT_SECRET_FILE} is empty"
    MLFLOW_MODE="client_credentials (client ${MLFLOW_CLIENT_ID})"
    log "==> MLflow client-credentials as ${MLFLOW_CLIENT_ID} (secret sha8 $(sha8 "$MLFLOW_CLIENT_SECRET"))"
else
    log "==> Reading MLflow read bearer from secret ${MLFLOW_TOKEN_SECRET} (rossoctl-system)..."
    MLFLOW_BEARER="$(kc -n rossoctl-system get secret "$MLFLOW_TOKEN_SECRET" \
        -o jsonpath='{.data.token}' 2>/dev/null | base64 -d 2>/dev/null || true)"
    if [ -n "$MLFLOW_BEARER" ]; then
        MLFLOW_MODE="bearer_token (secret ${MLFLOW_TOKEN_SECRET})"
        log "    bearer present (len ${#MLFLOW_BEARER}, sha8 $(sha8 "$MLFLOW_BEARER"))"
    else
        # Reported as a precheck FAILURE below, not just here: this used to be a warning, and the
        # file was written with an empty bearer anyway.
        warn "secret ${MLFLOW_TOKEN_SECRET} has no .data.token, and no MLflow credential was given."
        warn "         Either create it:"
        warn "           oc -n rossoctl-system create sa mlflow-reader"
        warn "           oc -n rossoctl-system apply -f - <<'Y'"
        warn "           apiVersion: v1"
        warn "           kind: Secret"
        warn "           metadata:"
        warn "             name: ${MLFLOW_TOKEN_SECRET}"
        warn "             annotations: {kubernetes.io/service-account.name: mlflow-reader}"
        warn "           type: kubernetes.io/service-account-token"
        warn "           Y"
        warn "         or supply the credential of the MLflow that already exists"
        warn "         (--mlflow-bearer-file / --mlflow-username / --mlflow-client-id),"
        warn "         or declare it unauthenticated with --mlflow-no-auth."
    fi
fi
case "$MLFLOW_INSECURE_TLS" in
    true|false) ;;
    *) die "--mlflow-insecure-tls must be true or false (got '${MLFLOW_INSECURE_TLS}')" ;;
esac

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

# --- 4. S3: declared, not discovered ---
# Whether runs publish is the installer's decision, so it is asked rather than inferred. This used to
# take the env pair, else --s3-from, else the LAST instances/*.json with keys in glob order — and with
# none of those it warned, defaulted the bucket anyway and wrote empty keys. The Service gates export
# on the bucket alone, so every run then attempted an upload that could not authenticate: a cluster
# that scores and publishes nothing, with the only trace an `S3 export failed` log line. Now
# S3_ENABLED is required and the precheck below proves the key (reference/s3check.py).
case "$S3_ENABLED" in true) log "==> S3 publishing declared ON" ;; false) log "==> S3 publishing declared OFF" ;; esac

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

# The IBAC judge. A WARNING, never a failure: legs #1-#4 and #9-#12 neither use nor need one, so a
# cluster with no judge is a perfectly good install. It is reported at all because its absence is
# otherwise SILENT — an empty judgeEndpoint fails no deploy and no run; the plugin legs complete with
# the ibac plugin inert, and the only tell is a judge call count of zero.
#
# THIS script never installs a judge — the CHART does, and that is the difference from the collector
# and MLflow below, which AutoBench only ever asserts. Rossoctl ships no judge (every ibac.* field
# arrives empty) and only the benchmark's plugin legs need one, so the judge belongs to AutoBench and
# `helm uninstall` takes it away again: `--set ibacJudge.enabled=true --set ibacJudge.upstreamBase=...
# --set ibacJudge.model=...`. This script runs BEFORE that install, to write the instance file, so
# here the fields are simply reported. Warning, not failure, for the same reason as above.
# Under autobench-install.sh --ibac-judge (IBAC_JUDGE=true) the Helm install that FOLLOWS creates the
# judge and patches the fields, so their absence now is expected; its post-install preflight checks them live.
IBAC_CFG="$(kc -n rossoctl-system get cm rossoctl-platform-config -o jsonpath='{.data.config\.yaml}' 2>/dev/null || true)"
if [ "${IBAC_JUDGE:-}" = true ]; then
    check "ibac judge — IBAC_JUDGE=true: the chart install that follows creates it and patches the fields" 0
elif [ -z "$IBAC_CFG" ]; then
    warn "rossoctl-platform-config not readable in rossoctl-system: cannot tell whether an IBAC judge is configured"
else
    J_EP="$(printf '%s\n' "$IBAC_CFG" | sed -n 's/^[[:space:]]*judgeEndpoint:[[:space:]]*"\{0,1\}\([^"]*\)"\{0,1\}[[:space:]]*$/\1/p' | head -1)"
    J_MODEL="$(printf '%s\n' "$IBAC_CFG" | sed -n 's/^[[:space:]]*judgeModel:[[:space:]]*"\{0,1\}\([^"]*\)"\{0,1\}[[:space:]]*$/\1/p' | head -1)"
    if [ -z "$J_EP" ] && [ -z "$J_MODEL" ]; then
        warn "ibac.judgeEndpoint and ibac.judgeModel are both empty: plugin legs #5-#8 would run with the ibac plugin INERT (zero judge calls, no error)"
    elif [ -z "$J_EP" ] || [ -z "$J_MODEL" ]; then
        warn "ibac judge half-configured (endpoint='${J_EP}' model='${J_MODEL}'): both are required"
    else
        check "ibac judge configured (model ${J_MODEL})" 0
        # The judge's key is its own, and the model must be one THAT gateway lists. Same base+model
        # pairing as the workload profile, and it fails the same silent way.
        kc -n rossoctl-system get secret ibac-judge-upstream >/dev/null 2>&1 \
            || warn "ibac-judge-upstream Secret absent in rossoctl-system: every judge call 401s"
    fi
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

# S3: the declaration, the shape of every value, and a signed read-only request that proves the key
# authenticates. Rows print in this script's own format; the exit status is the failure count.
S3_FAILS=0
"${PY[@]}" "$(dirname "${BASH_SOURCE[0]}")/s3check.py" || S3_FAILS=$?
PRECHECK_FAIL=$((PRECHECK_FAIL + S3_FAILS))

# An MLflow credential is REQUIRED unless the installer declares there is none. This was a warning
# until 2026-09-30, and the file was written with `bearer_token: ""` regardless — which produces the
# worst failure shape we have: the Service reads 403, every per-task token count reads 0, and the run
# still reports pass_rate 1.0. `--mlflow-no-auth` is the declaration, not the default, because "I
# forgot the credential" and "this MLflow has no auth" must not look the same to this script.
if [ "$MLFLOW_MODE" = "none" ]; then
    check "MLflow credential resolved" 1 \
        "no credential and no --mlflow-no-auth. Supply one (--mlflow-bearer-file, --mlflow-username + --mlflow-password-file, --mlflow-client-id + --mlflow-client-secret-file + --mlflow-token-url, or a populated ${MLFLOW_TOKEN_SECRET}), or declare the MLflow unauthenticated with --mlflow-no-auth"
else
    check "MLflow credential resolved: ${MLFLOW_MODE}" 0
fi

if [ "$PRECHECK_FAIL" -ne 0 ] && [ -z "$SKIP_PRECHECK" ]; then
    log ""
    die "${PRECHECK_FAIL} precheck failure(s) — fix the cluster, or pass --skip-precheck to write anyway"
fi

# --- 6. write instances/<encoded-iss-host>.json ---
ISS_HOST="${ISS#*://}"; ISS_HOST="${ISS_HOST%%/*}"
mkdir -p "$OUT_DIR"
OUT_FILE="${OUT_DIR%/}/${ISS_HOST//:/_}.json"

NO_PROXY_HOSTS="127.0.0.1,localhost,${WORKLOAD_LLM_API_BASE#*://},otel-collector.rossoctl-system.svc.cluster.local,keycloak.keycloak.svc.cluster.local"

# Every value reaches jq through the ENVIRONMENT, not through --arg: argv is world-readable (`ps`,
# /proc/<pid>/cmdline) and this file carries the ROPC password, the MLflow credential and the bucket
# keys. A process environment is readable by its own user only. Same mechanism as
# kind-service-bootstrap.sh; this script used --arg until 2026-09-30, which published the password to
# anyone who ran `ps` during the second jq took to run. Nothing here is ever echoed.
#
# The MLflow credential is emitted in ONE shape — the one resolved above — rather than as a union
# with empty strings. `auth/mlflow.py` picks by precedence, so a leftover empty field is harmless but
# a leftover NON-empty one silently outranks the shape the installer asked for.
export ISS ROSSOCTL_BASE_URL KC_SERVICE_CLIENT_ID KC_SERVICE_CLIENT_SECRET \
       KC_SERVICE_USERNAME KC_SERVICE_PASSWORD \
       MLFLOW_URL MLFLOW_BEARER MLFLOW_USERNAME MLFLOW_PASSWORD MLFLOW_OAUTH_URL \
       MLFLOW_CLIENT_ID MLFLOW_CLIENT_SECRET MLFLOW_TOKEN_URL \
       MLFLOW_EXPERIMENT_ID MLFLOW_WORKSPACE MLFLOW_INSECURE_TLS MLFLOW_NO_AUTH \
       S3_ENABLED S3_BUCKET S3_REGION S3_ACCESS_KEY_ID S3_SECRET_ACCESS_KEY S3_ENDPOINT_URL S3_PREFIX \
       ENDPOINT_TEMPLATE WORKLOAD_LLM_API_BASE WORKLOAD_LLM_MODEL NO_PROXY_HOSTS \
       WORKLOAD_OTEL_ENDPOINT WORKLOAD_OTEL_INSECURE WORKLOAD_AGENT_RUNNER
(
umask 077
jq -n '
    {
        iss: env.ISS,
        rossoctl_base_url: env.ROSSOCTL_BASE_URL,
        service_credential: {
            client_id: env.KC_SERVICE_CLIENT_ID,
            client_secret: (env.KC_SERVICE_CLIENT_SECRET // ""),
            username: env.KC_SERVICE_USERNAME,
            password: env.KC_SERVICE_PASSWORD
        },
        mlflow: (
            {
                tracking_url: env.MLFLOW_URL,
                experiment_id: env.MLFLOW_EXPERIMENT_ID,
                workspace: env.MLFLOW_WORKSPACE,
                insecure_tls: (env.MLFLOW_INSECURE_TLS == "true")
            }
            + (if   (env.MLFLOW_BEARER   // "") != "" then { bearer_token: env.MLFLOW_BEARER }
               elif (env.MLFLOW_PASSWORD // "") != "" then
                   { username: env.MLFLOW_USERNAME, password: env.MLFLOW_PASSWORD }
                   + (if (env.MLFLOW_OAUTH_URL // "") != ""
                      then { oauth_url: env.MLFLOW_OAUTH_URL } else {} end)
               elif (env.MLFLOW_CLIENT_SECRET // "") != "" then
                   { client_id: env.MLFLOW_CLIENT_ID,
                     client_secret: env.MLFLOW_CLIENT_SECRET,
                     token_url: env.MLFLOW_TOKEN_URL }
               elif (env.MLFLOW_NO_AUTH // "") != "" then
                   # An unauthenticated MLflow still needs a bearer_token key, and that is not a
                   # contradiction: mlflow_token() mints before it reads, and with no shape at all it
                   # raises, the route catches it, and the run fails soft into an EMPTY token report.
                   # A placeholder selects the static-bearer branch, which then sends a header the
                   # reader ignores. kind-service-bootstrap.sh writes the same value.
                   { bearer_token: "unused-no-auth-reader" }
               else {} end)
        ),
        workload_llm: {
            api_base: env.WORKLOAD_LLM_API_BASE,
            default_model: env.WORKLOAD_LLM_MODEL,
            no_proxy: env.NO_PROXY_HOSTS
        },
        workload_otel: {
            enabled: true, endpoint: env.WORKLOAD_OTEL_ENDPOINT, protocol: "http/protobuf",
            insecure: (env.WORKLOAD_OTEL_INSECURE == "true")
        },
        workload_agent_runner: env.WORKLOAD_AGENT_RUNNER
      }
      # No s3 key at all unless S3 is declared on: the Service then skips export by design
      # (bucket unset), instead of attempting one with empty keys and logging the failure.
      + (if env.S3_ENABLED == "true" then
            { s3: ({ bucket: env.S3_BUCKET, region: env.S3_REGION,
                     access_key_id: env.S3_ACCESS_KEY_ID,
                     secret_access_key: env.S3_SECRET_ACCESS_KEY,
                     prefix: env.S3_PREFIX }
                   + (if (env.S3_ENDPOINT_URL // "") != ""
                      then { endpoint_url: env.S3_ENDPOINT_URL } else {} end)) }
         else {} end)
      # Only set for a cross-cluster split (Service here, workloads there); when unset the Service
      # dials the co-located svc.cluster.local address.
      + (if (env.ENDPOINT_TEMPLATE // "") == "" then {}
         else { mcp_endpoint_template: env.ENDPOINT_TEMPLATE,
                agent_endpoint_template: env.ENDPOINT_TEMPLATE } end)' \
    > "$OUT_FILE"
)
chmod 600 "$OUT_FILE"
log ""
log "==> Wrote ${OUT_FILE}"
if [ "$S3_ENABLED" = "true" ]; then
    log "    s3: bucket ${S3_BUCKET:-} prefix ${S3_PREFIX} key sha8 $(sha8 "${S3_ACCESS_KEY_ID:-}")"
else
    log "    s3: none written — runs will publish no artifacts"
fi
if [ -n "$PRINT_OUT_FILE" ]; then
    printf '%s\n' "$OUT_FILE"
    exit 0
fi

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
