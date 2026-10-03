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

Required, from the environment or an --env-file (never a flag — see "S3" below):
  S3_ENABLED=true|false  whether runs publish artifacts. There is no default.

Flags (each also has an env fallback):
  --env-file FILE      KEY=VALUE lines (chmod 600), repeatable; see reference/autobench.env.template.
                                          Precedence, last wins: the shell, then each --env-file in
                                          order, then flags.
  --print-out-file     print only the written file's path on stdout (for autobench-install.sh)
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
  --mlflow-no-auth     MLFLOW_NO_AUTH=1   declare this MLflow unauthenticated — the same spelling
                                          ocp-service-bootstrap.sh takes. Without it, auth is
                                          INFERRED from the URL (a *mlflow-reader* URL is treated
                                          as no-auth) and the inference is logged.
  --copy-from FILE     COPY_FROM          existing instance JSON to carry `workload_llm` over from —
                                          an environment fact this script cannot discover, which a
                                          cluster rebuild should reproduce rather than lose. Its
                                          `s3` block is NOT carried: S3 is declared (see below).
  --llm-profile P      LLM_PROFILE        intranet|internet — selects the INTRANET_LLM_* or
                                          INTERNET_LLM_* variable set (see llm-profiles.sh). The
                                          gateway follows the cluster's NETWORK, not its platform.
  --llm-api-base URL   WORKLOAD_LLM_API_BASE  gateway origin; overrides the profile's
                                          (--llm-base is the old name for this flag)
  --llm-model M        WORKLOAD_LLM_MODEL default model, e.g. openai/<gateway model id>
  --otel-endpoint URL  WORKLOAD_OTEL_ENDPOINT  (default: the in-cluster collector on :8335)
  --agent-runner R     WORKLOAD_AGENT_RUNNER   direct|service (default: direct — `service` puts
                                          token spans on the wrong task at p > 1)
  --s3-prefix P        S3_PREFIX          (default: kind/; used only when S3_ENABLED=true)
  --out-dir DIR        OUT_DIR            where to write the config (default: ./instances)
  --kubectl BIN        KUBECTL_BIN        kubectl binary            (default: kubectl)
  --client ID          KC_SERVICE_CLIENT_ID  client the Service logs in through (default: rossoctl)
  --username U         KC_SERVICE_USERNAME   the user the Service logs in as (default: benchmarker)
  --password-file F    the benchmarker password from a chmod-600 file. PREFERRED — the value never
                                          reaches argv, so `ps` cannot see it and it is not left in
                                          shell history.
  --password-stdin     read the password from stdin (pipeline or secret manager)
  --password P         the password on the command line — accepted, but argv is world-readable
  -h, --help

MLflow on kind: the `mlflow` Deployment rossoctl-deps installs runs mlflow-oidc-auth, which
refuses both the Service's read and the collector's write. deploy/kind/mlflow-reader.yaml serves
the same postgres with no auth, and this script points the Service at it by default. Wire the
collector to it too, or spans are dropped with a 401 and the run publishes zeros:
  uv run python reference/kind-collector-mlflow.py

The Service authenticates to Rossoctl with ITS OWN per-instance credential
(ROPC / password grant). In kind the login/JWKS URLs are taken from the Keycloak
BACKCHANNEL URL (not composed from iss — the iss host is unreachable in-cluster);
iss is stored for identity matching only. That credential is the `benchmarker`
identity — per-instance by design, seeded here as a deploy-time default and
overridable at runtime via the benchmarker config API. The generated file
therefore carries it:
  KC_SERVICE_USERNAME        login username     (required; --username overrides)
  KC_SERVICE_PASSWORD        login password     (required unless a --password* flag is given)
  KC_SERVICE_CLIENT_SECRET   client secret, if confidential (optional)
No cluster credential is written — Rossoctl performs cluster ops server-side.

S3 — environment only (an --env-file is the intended carrier), never argv, never copied from a file:
  S3_ENABLED            true|false, REQUIRED. false writes no s3 block: runs score, publish nothing.
  S3_BUCKET, S3_REGION, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY
                        all required when S3_ENABLED=true; the key is proven by a signed read-only
                        request (reference/s3check.py) before the file is written
  S3_ENDPOINT_URL       optional, an S3-compatible store's http(s)://host[:port]
SKIP_S3_CHECK=1 skips only that live request; the declaration and the shape are always checked.

Because the generated file BAKES that password in, the script logs in with it before writing:
every /deploy the Service makes is a ROPC login as this user, so a wrong one produces an install
that looks healthy and a 502 wrapping a 403 on every single run. Set SKIP_CRED_CHECK=1 to write
the file anyway (only useful when Keycloak is not reachable from here yet).
EOF
}

log()  { printf '%s\n' "$*" >&2; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'Error: %s\n' "$*" >&2; exit 1; }

# --- dependencies ---
command -v curl    >/dev/null || die "curl is required"
command -v jq      >/dev/null || die "jq is required"
# shellcheck source=reference/pyrun.sh
. "$(dirname "${BASH_SOURCE[0]}")/pyrun.sh"
repo_python "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || die "$REPO_PYTHON_HINT"

# Before ANY default below reads the environment: that ordering is what makes an --env-file beat the
# shell while still losing to a flag.
# shellcheck source=reference/envfile.sh
. "$(dirname "${BASH_SOURCE[0]}")/envfile.sh"
envfile_prescan "$@" || exit 1

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
# Captured before MLFLOW_NO_AUTH is computed below, so "the caller declared it" stays distinguishable
# from "this script worked it out" — the whole point of the flag.
MLFLOW_NO_AUTH_DECLARED="${MLFLOW_NO_AUTH:-}"
COPY_FROM="${COPY_FROM:-}"
# WORKLOAD_LLM_API_BASE is the canonical name — it matches the instance-file field this script
# writes (workload_llm.api_base) and the OpenShift sibling's variable. WORKLOAD_LLM_BASE is accepted
# as an alias: it was never a deliberate second name, just this flag being added a day after the
# OpenShift one and named after the flag (--llm-base) rather than the field.
WORKLOAD_LLM_API_BASE="${WORKLOAD_LLM_API_BASE:-${WORKLOAD_LLM_BASE:-}}"
WORKLOAD_LLM_MODEL="${WORKLOAD_LLM_MODEL:-}"
WORKLOAD_OTEL_ENDPOINT="${WORKLOAD_OTEL_ENDPOINT:-http://otel-collector.rossoctl-system.svc.cluster.local:8335}"
# `direct`: see the same default in ocp-service-bootstrap.sh for the measurement behind it.
WORKLOAD_AGENT_RUNNER="${WORKLOAD_AGENT_RUNNER:-direct}"
S3_PREFIX="${S3_PREFIX:-kind/}"
S3_ENABLED="${S3_ENABLED:-}"
SKIP_S3_CHECK="${SKIP_S3_CHECK:-}"
PRINT_OUT_FILE=""
OUT_DIR="${OUT_DIR:-./instances}"
KUBECTL_BIN="${KUBECTL_BIN:-kubectl}"
KC_SERVICE_CLIENT_ID="${KC_SERVICE_CLIENT_ID:-rossoctl}"
KC_SERVICE_USERNAME="${KC_SERVICE_USERNAME:-benchmarker}"
KC_SERVICE_PASSWORD="${KC_SERVICE_PASSWORD:-}"
KC_SERVICE_CLIENT_SECRET="${KC_SERVICE_CLIENT_SECRET:-}"
SKIP_CRED_CHECK="${SKIP_CRED_CHECK:-}"
CRED_PASSWORD_FILE=""
CRED_PASSWORD_STDIN=""
CRED_PASSWORD_ARGV=""

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
        --mlflow-no-auth)   MLFLOW_NO_AUTH_DECLARED=1; shift ;;
        --copy-from)        COPY_FROM="$2"; shift 2 ;;
        --llm-profile)      LLM_PROFILE="$2"; shift 2 ;;
        --llm-api-base|--llm-base) WORKLOAD_LLM_API_BASE="$2"; shift 2 ;;
        --llm-model)        WORKLOAD_LLM_MODEL="$2"; shift 2 ;;
        --otel-endpoint)    WORKLOAD_OTEL_ENDPOINT="$2"; shift 2 ;;
        --agent-runner)     WORKLOAD_AGENT_RUNNER="$2"; shift 2 ;;
        --s3-prefix)        S3_PREFIX="$2"; shift 2 ;;
        --env-file)         shift 2 ;;   # already loaded by envfile_prescan
        --env-file=*)       shift ;;
        --print-out-file)   PRINT_OUT_FILE=1; shift ;;
        --out-dir)          OUT_DIR="$2"; shift 2 ;;
        --kubectl)          KUBECTL_BIN="$2"; shift 2 ;;
        --client)           KC_SERVICE_CLIENT_ID="$2"; shift 2 ;;
        --username)         KC_SERVICE_USERNAME="$2"; shift 2 ;;
        --password-file)    CRED_PASSWORD_FILE="$2"; shift 2 ;;
        --password-stdin)   CRED_PASSWORD_STDIN=1; shift ;;
        --password)         CRED_PASSWORD_ARGV="$2"; shift 2 ;;
        -h|--help)          usage; exit 0 ;;
        *)                  usage; die "unknown argument '$1'" ;;
    esac
done

command -v "$KUBECTL_BIN" >/dev/null || die "$KUBECTL_BIN is required"

# A selected profile fills in whatever the flags did not: base, model, key file, bypass list.
# shellcheck source=reference/llm-profiles.sh
. "$(dirname "${BASH_SOURCE[0]}")/llm-profiles.sh"
llm_profile_resolve || die "could not resolve LLM_PROFILE=${LLM_PROFILE:-}"
[ -n "$KC_SERVICE_USERNAME" ] || die "--username (or KC_SERVICE_USERNAME) must not be empty"

# shellcheck source=reference/credfile.sh
. "$(dirname "${BASH_SOURCE[0]}")/credfile.sh"
CRED_N=0
[ -n "$CRED_PASSWORD_FILE" ]  && CRED_N=$((CRED_N+1))
[ -n "$CRED_PASSWORD_STDIN" ] && CRED_N=$((CRED_N+1))
[ -n "$CRED_PASSWORD_ARGV" ]  && CRED_N=$((CRED_N+1))
[ "$CRED_N" -le 1 ] || die "--password-file, --password-stdin and --password are mutually exclusive"
CRED_RC=0; cred_resolve_password KC_SERVICE_PASSWORD || CRED_RC=$?   # `; rc=$?` trips set -e first
case "$CRED_RC" in
    0) ;;
    2) die "no password for ${KC_SERVICE_USERNAME}: pass --password-file <chmod-600 file>, --password-stdin, --password, or set KC_SERVICE_PASSWORD" ;;
    *) exit 1 ;;   # cred_read_file already said why
esac
[ -n "$KUBE_CONTEXT" ] || KUBE_CONTEXT="kind-${KIND_CLUSTER_NAME}"

# --- 0. S3: declared, not discovered ---
# Publishing is optional, but forgetting it and opting out used to look the same: the s3 block rode in
# on --copy-from or was absent, and either way nothing failed. Now S3_ENABLED must be said out loud,
# and `true` must come with a key that authenticates. First, so a missing declaration costs nothing.
export S3_ENABLED S3_BUCKET S3_REGION S3_ACCESS_KEY_ID S3_SECRET_ACCESS_KEY S3_ENDPOINT_URL S3_PREFIX
log "==> Checking the S3 declaration..."
S3_FAILS=0
"${PY[@]}" "$(dirname "${BASH_SOURCE[0]}")/s3check.py" ${SKIP_S3_CHECK:+--offline} || S3_FAILS=$?
[ "$S3_FAILS" -eq 0 ] || die "S3 is not usable as declared (see above) — fix the S3_* values, or declare S3_ENABLED=false"
[ -z "$SKIP_S3_CHECK" ] || [ "$S3_ENABLED" != true ] || warn "SKIP_S3_CHECK=1 — the S3 key was NOT proven to authenticate"

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

# --- 1b. the credential this file will BAKE IN actually logs in ---
#
# Against the ingress host rather than the backchannel URL, because the backchannel is svc DNS and
# unreachable from here; both reach the same realm, and it is the realm's answer we want.
#
# This is the one check the script cannot skip without making its output untrustworthy: the password
# lands in the instance file, the Service presents it on every /deploy, and a wrong one fails no
# earlier than the first benchmark — as a 502 wrapping Keycloak's 403, which reads like a Rossoctl
# problem. keycloak-ensure-user.sh sets the password; kind-post-setup.sh then patches the
# firstName/lastName the realm's user profile requires, and only AFTER that patch can a login
# succeed — hence the check belongs here, at the end of the chain, not in either of those.
log "==> Verifying the ${KC_SERVICE_USERNAME} credential (${CRED_PASSWORD_SOURCE}, sha8 $(printf '%s' "$KC_SERVICE_PASSWORD" | shasum -a 256 | cut -c1-8))..."
if [ -n "$SKIP_CRED_CHECK" ]; then
    warn "SKIP_CRED_CHECK=1 — not verifying the password; every /deploy will fail if it is wrong"
else
    req POST "http://${KC_HOST}/realms/${KC_REALM}/protocol/openid-connect/token" \
        -d client_id="$KC_SERVICE_CLIENT_ID" \
        ${KC_SERVICE_CLIENT_SECRET:+-d client_secret="$KC_SERVICE_CLIENT_SECRET"} \
        -d grant_type=password -d username="$KC_SERVICE_USERNAME" \
        --data-urlencode "password=${KC_SERVICE_PASSWORD}"
    CRED_TOK="$(printf '%s' "$BODY" | jq -r '.access_token // empty' 2>/dev/null || true)"
    if [ -n "$CRED_TOK" ]; then
        log "    ROPC login ok"
        # The realm role every /deploy needs; without it the Service surfaces Keycloak's 403 as a 502.
        CRED_ROLES="$(cred_jwt_realm_roles "$CRED_TOK")"
        case ",$CRED_ROLES," in
            *,rossoctl-operator,*) log "    realm role rossoctl-operator ok" ;;
            *) warn "the token carries no rossoctl-operator realm role — every /deploy will 403; grant it (kind-post-setup.sh does)" ;;
        esac
    elif [ "$HTTP_STATUS" = "000" ]; then
        warn "Keycloak at ${KC_HOST} did not answer, so the password is unverified"
    else
        die "$(printf '%s password rejected by realm %s: %s' "$KC_SERVICE_USERNAME" "$KC_REALM" "$(cred_ropc_cause "$HTTP_STATUS" "$BODY")")"
    fi
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
# as a refused write, so a guess here is indistinguishable from a broken telemetry chain. A lookup
# that cannot run is therefore fatal, not a default: it used to fall back to 0 with its stderr
# discarded, which on a python3 without PyYAML meant every install guessed, silently.
if [ -z "$MLFLOW_EXPERIMENT_ID" ]; then
    MLFLOW_EXPERIMENT_ID="$("${PY[@]}" "$(dirname "${BASH_SOURCE[0]}")/kind-collector-mlflow.py" \
        --context "$KUBE_CONTEXT" --print-experiment-id)" \
        || die "could not read the collector's x-mlflow-experiment-id (above) — fix the collector, or pass --experiment-id N"
    [[ "$MLFLOW_EXPERIMENT_ID" =~ ^[0-9]+$ ]] \
        || die "the collector's x-mlflow-experiment-id is not a number — pass --experiment-id N"
    log "    experiment id ${MLFLOW_EXPERIMENT_ID} read from the collector's MLflow exporter"
fi

# `workload_llm` is an environment fact this script cannot discover — which gateway issued the key in
# openai-secret. Carry it over from an existing instance file so a cluster rebuild reproduces it
# instead of silently dropping it. The file's `s3` block is deliberately NOT carried: S3 is declared
# (step 0), and a block inherited from whichever file happened to be there is how a cluster ended up
# publishing with a key nobody chose — or with none.
LLM_JSON="null"
if [ -n "$COPY_FROM" ]; then
    [ -f "$COPY_FROM" ] || { echo "--copy-from: no such file: $COPY_FROM" >&2; exit 1; }
    # Values move through jq only; nothing is echoed.
    LLM_JSON="$(jq -c '.workload_llm // null' "$COPY_FROM")"
    [ "$LLM_JSON" = null ] && warn "--copy-from has no .workload_llm — runs will use the benchmark default model"
fi
if [ -n "$WORKLOAD_LLM_API_BASE" ] || [ -n "$WORKLOAD_LLM_MODEL" ]; then
    # The proxy-bypass list is part of the gateway's configuration, not a separate concern: where the
    # cluster injects an egress proxy, an in-network gateway missing from this list is unreachable.
    # Only computed when a base is being set here, and never over a list carried in by --copy-from.
    LLM_NO_PROXY=""
    if [ -n "$WORKLOAD_LLM_API_BASE" ]; then
        # Bypass entries are HOSTS: strip scheme, then port, then path from each URL.
        host_of() { local h="${1#*://}"; h="${h%%/*}"; printf '%s' "${h%%:*}"; }
        LLM_NO_PROXY="$(llm_profile_no_proxy \
            "$(host_of "$WORKLOAD_OTEL_ENDPOINT")" "$(host_of "$KC_BACKCHANNEL_URL")")"
    fi
    LLM_JSON="$(jq -cn --argjson cur "$LLM_JSON" --arg b "$WORKLOAD_LLM_API_BASE" \
        --arg m "$WORKLOAD_LLM_MODEL" --arg np "$LLM_NO_PROXY" \
        '($cur // {}) | (if $b != "" then .api_base = $b else . end)
                      | (if $m != "" then .default_model = $m else . end)
                      | (if $b != "" and (.no_proxy // "") == "" then .no_proxy = $np else . end)')"
fi
if [ "$LLM_JSON" = null ]; then
    warn "no workload_llm (pass --llm-profile, --copy-from, or --llm-api-base/--llm-model): the agent"
    warn "  will use the benchmark's baked-in default model, which the cluster's key may not reach"
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
#
# Whether this MLflow authenticates is DECLARED by --mlflow-no-auth, and only INFERRED from the URL
# when nothing was declared. The inference is right for every default kind cluster and wrong for any
# other — and getting it wrong fails soft, with an empty token report on a run that passes — so it is
# logged rather than made silently. An MLflow that already exists is the case it cannot get right.
if [ -n "$MLFLOW_NO_AUTH_DECLARED" ]; then
    MLFLOW_NO_AUTH=1
    log "==> MLflow declared unauthenticated (--mlflow-no-auth)"
else
    case "$MLFLOW_URL" in
        *mlflow-reader*) MLFLOW_NO_AUTH=1 ;;
        *)               MLFLOW_NO_AUTH=0 ;;
    esac
    if [ "$MLFLOW_NO_AUTH" = 1 ]; then
        log "==> MLflow auth INFERRED from the URL: none, it is an mlflow-reader"
    else
        log "==> MLflow auth INFERRED from the URL: client-credentials from mlflow-oauth-secret"
    fi
    log "    pass --mlflow-no-auth (or point --mlflow-url at an authenticating MLflow) to declare it"
fi

# Every value reaches jq through the ENVIRONMENT, not through --arg: argv is world-readable
# (`ps`, /proc/<pid>/cmdline) and this file carries the ROPC password and the bucket keys. The
# environment of a process is readable by its own user only. Nothing here is ever echoed.
# The file is created 600 by umask, not chmod'd after the fact — there is no window in which the
# credentials sit world-readable on disk.
export ISS KC_BACKCHANNEL_URL ROSSOCTL_BASE_URL \
       KC_SERVICE_CLIENT_ID KC_SERVICE_CLIENT_SECRET KC_SERVICE_USERNAME KC_SERVICE_PASSWORD \
       MLFLOW_URL MLFLOW_CLIENT_ID MLFLOW_CLIENT_SECRET MLFLOW_TOKEN_URL MLFLOW_EXPERIMENT_ID \
       MLFLOW_NO_AUTH LLM_JSON WORKLOAD_OTEL_ENDPOINT WORKLOAD_AGENT_RUNNER
(
umask 077
jq -n '
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
    # S3_ENABLED=false writes NO s3 key: the Service then has bucket=None and skips export by design,
    # rather than attempting an upload that cannot authenticate.
    + (if env.S3_ENABLED == "true" then
          { s3: ({ bucket: env.S3_BUCKET, region: env.S3_REGION,
                   access_key_id: env.S3_ACCESS_KEY_ID,
                   secret_access_key: env.S3_SECRET_ACCESS_KEY,
                   prefix: env.S3_PREFIX }
                 + (if (env.S3_ENDPOINT_URL // "") != ""
                    then { endpoint_url: env.S3_ENDPOINT_URL } else {} end)) }
       else {} end)
    + (if $llm then { workload_llm: $llm } else {} end)' > "$OUT_FILE"
)
chmod 600 "$OUT_FILE"
log "==> Wrote ${OUT_FILE}"
if [ "$S3_ENABLED" = "true" ]; then
    log "    s3: bucket ${S3_BUCKET:-} prefix ${S3_PREFIX} key sha8 $(printf '%s' "${S3_ACCESS_KEY_ID:-}" | shasum -a 256 | cut -c1-8)"
else
    log "    s3: none written (S3_ENABLED=false) — runs will publish no artifacts"
fi
if [ -n "$PRINT_OUT_FILE" ]; then
    printf '%s\n' "$OUT_FILE"
    exit 0
fi

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
