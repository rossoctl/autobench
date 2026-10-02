#!/usr/bin/env bash
# Install or upgrade AutoBench on one cluster: preflight -> instance config -> Secret -> Helm -> verify.
#
#   reference/autobench-install.sh --env-file ~/.rossoctl-<cluster>/autobench.env
#   reference/autobench-install.sh --platform openshift --context <ctx> --cluster ykt5 \
#       --env-file base.env --env-file override.env --dry-run
#
# Start from reference/autobench.env.template. Every input is an environment variable, so it can come
# from the shell, from one or more --env-file (later wins), or — for the few that have one — a flag,
# which beats both. Credentials only ever come from the environment or from chmod-600 files.
#
# What it does, in order — and it stops at the first thing that is wrong:
#
#   1. tools        kubectl, helm, jq, python3, curl (~/.rd/bin is added to PATH)
#   2. inputs       platform, context, S3_ENABLED declared, the judge's inputs shaped right
#   3. preflight    reference/preflight.py --pre-install must report 0 failures. What the release
#                   itself creates counts as present, and the MLflow round trip is skipped (it goes
#                   through the Service, which may not be installed yet) — step 8 checks both live
#   3b. MLflow      kind only: reference/kind-mlflow.sh installs mlflow-reader and points the
#                   collector at it — unless something already serves MLFLOW_URL (INSTALL_MLFLOW=auto)
#                   — and RECORDS that it did, so autobench-uninstall.sh removes exactly that
#   4. config       the platform's bootstrap script writes the instance file — it re-proves the
#                   benchmarker login and the S3 key itself
#   5. Secrets      autobench-instances gets that ONE key replaced (other keys are kept and named);
#                   with the judge, ibac-judge-upstream is written from IBAC_JUDGE_KEY_FILE
#   6. helm         helm upgrade --install ... --wait
#   7. restart      only if the Deployment existed before: a Secret change is read at pod start
#   8. verify       /healthz, the S3 block read BACK from the live Secret, and preflight.py again —
#                   this time with the MLflow round trip, the one check that crosses the whole chain
#
# --dry-run runs 1-4 for real (they write nothing to the cluster; the instance file goes to a temp
# dir) and prints 3b and 5-7 instead of running them.
#
# On a freshly rebuilt KinD cluster run reference/kind-post-setup.sh first: it seeds the realm user
# and the team secrets, which this script checks but does not create. The MLflow read path is the
# exception — this script installs it (3b), and a reader kind-post-setup.sh made is reused, not owned.
#
# Secrets never pass through argv: Secret manifests are built by jq from the environment into a
# chmod-600 temp file, and nothing prints a value — key ids appear as a sha8 at most.

set -euo pipefail
set +x   # never trace: the environment carries credentials

REFERENCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$REFERENCE_DIR")"

log()  { printf '%s\n' "$*" >&2; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'Error: %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: reference/autobench-install.sh [--env-file FILE]... [flags]

  --env-file FILE     load KEY=VALUE pairs (repeatable; later files win, flags beat files)
  --platform P        AB_PLATFORM     openshift | kind                                  (required)
  --context CTX       KUBE_CONTEXT    kubectl context of the Service's cluster          (required)
  --cluster NAME      CLUSTER         openshift: short name, e.g. ykt5                  (required there)
                                      kind: the kind cluster name (default rossoctl)
  --values FILE       HELM_VALUES     default deploy/helm/values-<platform>.yaml
  --image-tag T       IMAGE_TAG       default: the chart's appVersion
  --ibac-judge        IBAC_JUDGE=true install the IBAC judge (plugin legs #5-#8 only); needs
                                      IBAC_JUDGE_KEY_FILE, IBAC_JUDGE_UPSTREAM_BASE, IBAC_JUDGE_MODEL
  --no-ibac-judge                     acknowledge REMOVING a judge the live release has
  --install-mlflow M  INSTALL_MLFLOW  kind: auto (default) | always | never — see kind-mlflow.sh
  --dry-run                           checks for real, cluster writes printed instead of run
  --skip-preflight                    skip step 3 (the bootstrap's own checks still run)
  -h, --help

Required in the environment either way: S3_ENABLED=true|false (with S3_* when true), and the
benchmarker password as KC_SERVICE_PASSWORD or KC_SERVICE_PASSWORD_FILE. See
reference/autobench.env.template for every value.
EOF
}

# --- env files first: shell < files < flags --------------------------------------------------------
# shellcheck source=reference/envfile.sh
. "$REFERENCE_DIR/envfile.sh"
envfile_prescan "$@" || exit 1

AB_PLATFORM="${AB_PLATFORM:-}"
KUBE_CONTEXT="${KUBE_CONTEXT:-}"
CLUSTER="${CLUSTER:-}"
HELM_VALUES="${HELM_VALUES:-}"
IMAGE_TAG="${IMAGE_TAG:-}"
IBAC_JUDGE="${IBAC_JUDGE:-}"
INSTALL_MLFLOW="${INSTALL_MLFLOW:-auto}"
NAMESPACE="${NAMESPACE:-rossoctl-system}"
RELEASE="autobench"
CHART="$REPO_DIR/deploy/helm/autobench"
INSTANCES_SECRET="autobench-instances"
JUDGE_SECRET="ibac-judge-upstream"
DEPLOY="autobench-service"
DRY_RUN=""
SKIP_PREFLIGHT=""
DROP_JUDGE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --env-file)       shift 2 ;;
        --env-file=*)     shift ;;
        --platform)       AB_PLATFORM="${2:-}"; shift 2 ;;
        --context)        KUBE_CONTEXT="${2:-}"; shift 2 ;;
        --cluster)        CLUSTER="${2:-}"; shift 2 ;;
        --values)         HELM_VALUES="${2:-}"; shift 2 ;;
        --image-tag)      IMAGE_TAG="${2:-}"; shift 2 ;;
        --ibac-judge)     IBAC_JUDGE=true; shift ;;
        --no-ibac-judge)  IBAC_JUDGE=false; DROP_JUDGE=1; shift ;;
        --install-mlflow) INSTALL_MLFLOW="${2:-}"; shift 2 ;;
        --dry-run)        DRY_RUN=1; shift ;;
        --skip-preflight) SKIP_PREFLIGHT=1; shift ;;
        -h|--help)        usage; exit 0 ;;
        *) usage >&2; die "unknown argument: $1" ;;
    esac
done

# --- 1. tools ----------------------------------------------------------------------------------------
[ -d "$HOME/.rd/bin" ] && PATH="$PATH:$HOME/.rd/bin"
missing=()
for t in kubectl helm jq python3 curl; do command -v "$t" >/dev/null 2>&1 || missing+=("$t"); done
[ ${#missing[@]} -eq 0 ] || die "not on PATH: ${missing[*]}"

# --- 2. inputs ---------------------------------------------------------------------------------------
case "$AB_PLATFORM" in
    openshift|kind) ;;
    '') die "no platform — pass --platform openshift|kind or set AB_PLATFORM" ;;
    *)  die "AB_PLATFORM must be openshift or kind (got '$AB_PLATFORM')" ;;
esac
[ -n "$KUBE_CONTEXT" ] || die "no context — pass --context or set KUBE_CONTEXT (never defaulted: the current context is not necessarily the cluster you mean)"
# Captured first: `grep -q` exits at the first match, and under pipefail kubectl's SIGPIPE fails the test.
CONTEXTS="$(kubectl config get-contexts -o name)"
printf '%s\n' "$CONTEXTS" | grep -Fxq -- "$KUBE_CONTEXT" \
    || die "no kubectl context named '$KUBE_CONTEXT' (kubectl config get-contexts -o name)"
if [ "$AB_PLATFORM" = openshift ]; then
    [ -n "$CLUSTER" ] || die "--cluster (or CLUSTER) is required on openshift — it names the apps/Keycloak hosts"
else
    CLUSTER="${CLUSTER:-rossoctl}"
fi
HELM_VALUES="${HELM_VALUES:-$REPO_DIR/deploy/helm/values-$AB_PLATFORM.yaml}"
[ -f "$HELM_VALUES" ] || die "no values file at $HELM_VALUES"
# One declaration of the gateway, shared by the release and the instance config the bootstrap writes.
if [ -z "${LLM_PROFILE:-}" ]; then
    LLM_PROFILE="$(sed -n 's/^llmProfile:[[:space:]]*\([a-z]*\).*/\1/p' "$HELM_VALUES" | head -1)"
fi
export LLM_PROFILE
case "${S3_ENABLED:-}" in
    true|false) ;;
    '') die "S3_ENABLED is unset — declare S3_ENABLED=true (publish artifacts, every S3_* required) or S3_ENABLED=false (publish nothing). See reference/autobench.env.template" ;;
    *)  die "S3_ENABLED must be exactly true or false (got '${S3_ENABLED}')" ;;
esac
case "$IBAC_JUDGE" in ''|true|false) ;; *) die "IBAC_JUDGE must be true or false (got '$IBAC_JUDGE')" ;; esac
# IBAC_JUDGE too: the bootstrap reports the judge's absence differently when this install creates it.
export KUBE_CONTEXT CLUSTER S3_ENABLED IBAC_JUDGE INSTALL_MLFLOW

# kind: whether this install creates the MLflow read path — preflight must know before it judges it.
MLFLOW_PLAN=""
if [ "$AB_PLATFORM" = kind ]; then
    MLFLOW_PLAN="$("$REFERENCE_DIR/kind-mlflow.sh" plan --context "$KUBE_CONTEXT")" \
        || die "could not decide the MLflow plan (see above)"
fi

K=(kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE")
H=(--kube-context "$KUBE_CONTEXT" -n "$NAMESPACE")

# A release that already runs the judge loses it on an upgrade that does not ask for it — and the
# plugin legs then fail with a judge that no longer exists. That has to be a decision, not a default.
LIVE_JUDGE="$(helm get values "$RELEASE" "${H[@]}" -o json 2>/dev/null | jq -r '.ibacJudge.enabled // false' 2>/dev/null || echo false)"
if [ "$LIVE_JUDGE" = true ] && [ "$IBAC_JUDGE" != true ] && [ -z "$DROP_JUDGE" ]; then
    die "the live release runs the IBAC judge — pass --ibac-judge (IBAC_JUDGE=true) to keep it, or --no-ibac-judge to remove it"
fi

if [ "$IBAC_JUDGE" = true ]; then
    for v in IBAC_JUDGE_KEY_FILE IBAC_JUDGE_UPSTREAM_BASE IBAC_JUDGE_MODEL; do
        [ -n "${!v:-}" ] || die "IBAC_JUDGE=true but $v is unset (see reference/autobench.env.template)"
    done
    # Shape, not presence — each of these passed a presence check while failing every judge call.
    IBAC_JUDGE_UPSTREAM_BASE="$IBAC_JUDGE_UPSTREAM_BASE" IBAC_JUDGE_MODEL="$IBAC_JUDGE_MODEL" \
    python3 - "$REFERENCE_DIR" <<'PY' || exit 1
import os, sys, urllib.parse
sys.path.insert(0, sys.argv[1])
from preflight import LITELLM_PROVIDER_PREFIXES
base, model, bad = os.environ["IBAC_JUDGE_UPSTREAM_BASE"], os.environ["IBAC_JUDGE_MODEL"], []
u = urllib.parse.urlsplit(base)
if u.scheme not in ("http", "https") or not u.netloc or u.path.rstrip("/") or u.query:
    bad.append("IBAC_JUDGE_UPSTREAM_BASE must be a BASE url, http(s)://host[:port] with no path — "
               "the plugin appends /v1/chat/completions itself, so a path is sent twice and 404s")
head = model.split("/")[0]
if "/" in model and head in LITELLM_PROVIDER_PREFIXES:
    bad.append(f"IBAC_JUDGE_MODEL starts with the litellm prefix '{head}/' — it is sent verbatim as "
               f"the wire model, so use the bare catalogue id (likely '{model.split('/', 1)[1]}')")
for b in bad:
    print(f"Error: {b}", file=sys.stderr)
sys.exit(1 if bad else 0)
PY
    [ -f "$IBAC_JUDGE_KEY_FILE" ] || die "IBAC_JUDGE_KEY_FILE: no such file: $IBAC_JUDGE_KEY_FILE"
fi

log "AutoBench install — platform=$AB_PLATFORM context=$KUBE_CONTEXT cluster=$CLUSTER namespace=$NAMESPACE"
log "  values $HELM_VALUES   image tag ${IMAGE_TAG:-(chart appVersion)}   judge ${IBAC_JUDGE:-false}   s3 $S3_ENABLED"
[ -n "$MLFLOW_PLAN" ] && log "  mlflow $MLFLOW_PLAN (INSTALL_MLFLOW=$INSTALL_MLFLOW)"
[ -n "${ENVFILE_LOADED:-}" ] && log "  env files: $ENVFILE_LOADED"
[ -n "$DRY_RUN" ] && log "  DRY RUN — checks run for real; cluster writes are printed, not run"

TMP="$(umask 077; mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Run a cluster write, or print it under --dry-run. Only ever handed argv that carries no secret.
run() {
    if [ -n "$DRY_RUN" ]; then printf '[dry-run] %s\n' "$*" >&2; else "$@"; fi
}

# --- 3. preflight ------------------------------------------------------------------------------------
if [ -n "$SKIP_PREFLIGHT" ]; then
    warn "--skip-preflight — the cluster was NOT audited before writing to it"
else
    log ""
    log "==> 3. preflight (pre-install)"
    # --pre-install: what this release creates (the trace-writer RoleBinding; with the judge, its
    # Deployment, Secret and fields) is absent before a fresh install, so it is checked AFTER, below.
    PRE=(--platform "$AB_PLATFORM" --context "$KUBE_CONTEXT" --namespace "$NAMESPACE"
         --values "$HELM_VALUES" --chart "$CHART" --pre-install --skip-mlflow-probe)
    [ "$IBAC_JUDGE" = true ] && PRE+=(--ibac-judge)
    [ "$MLFLOW_PLAN" = install ] && PRE+=(--kind-mlflow)
    python3 "$REFERENCE_DIR/preflight.py" "${PRE[@]}" \
        || die "preflight reports failures — fix them, then re-run (nothing was written)"
fi

# --- 3b. the MLflow read path (kind) -----------------------------------------------------------------
# Before the bootstrap: it reads the collector's experiment id and warns when the reader is absent.
if [ "$AB_PLATFORM" = kind ]; then
    log ""
    log "==> 3b. MLflow read path ($MLFLOW_PLAN)"
    "$REFERENCE_DIR/kind-mlflow.sh" install --context "$KUBE_CONTEXT" ${DRY_RUN:+--dry-run} \
        || die "the MLflow install failed (see above) — re-run; autobench-uninstall.sh undoes a partial one"
fi

# --- 4. the instance config --------------------------------------------------------------------------
log ""
log "==> 4. instance config"
if [ -n "$DRY_RUN" ]; then OUT_DIR="$TMP/instances"; else OUT_DIR="${OUT_DIR:-$REPO_DIR/instances}"; fi
export OUT_DIR
if [ "$AB_PLATFORM" = openshift ]; then
    BOOT=("$REFERENCE_DIR/ocp-service-bootstrap.sh" --cluster "$CLUSTER" --context "$KUBE_CONTEXT")
else
    BOOT=("$REFERENCE_DIR/kind-service-bootstrap.sh" --cluster "$CLUSTER" --context "$KUBE_CONTEXT")
fi
INSTANCE_FILE="$("${BOOT[@]}" --out-dir "$OUT_DIR" --print-out-file)" \
    || die "the bootstrap failed (see above) — nothing was written to the cluster"
[ -f "$INSTANCE_FILE" ] || die "the bootstrap printed '$INSTANCE_FILE', which is not a file"
INSTANCE_KEY="$(basename "$INSTANCE_FILE")"
log "    instance file $INSTANCE_FILE -> Secret key $INSTANCE_KEY"

# --- 5. the Secrets ----------------------------------------------------------------------------------
# Write a Secret holding `data` (base64 values) merged over whatever keys it already has.
# $1 name, $2 key, $3 file holding the raw value. The value travels file -> base64 -> jq's env.
apply_secret_key() {
    local name="$1" key="$2" src="$3" existing manifest="$TMP/$1.json" verb
    existing="$("${K[@]}" get secret "$name" -o json 2>/dev/null || true)"
    if [ -n "$existing" ]; then verb=replace; else verb=create; existing='{}'; fi
    NEW_KEY="$key" NEW_B64="$(base64 < "$src" | tr -d '\n')" NS="$NAMESPACE" NAME="$name" \
        jq '{apiVersion: "v1", kind: "Secret", type: (.type // "Opaque"),
             metadata: {name: env.NAME, namespace: env.NS, labels: (.metadata.labels // {})},
             data: ((.data // {}) + {(env.NEW_KEY): env.NEW_B64})}' <<<"$existing" > "$manifest"
    local others
    others="$(jq -r --arg k "$key" '[.data | keys[] | select(. != $k)] | join(", ")' "$manifest")"
    log "    secret/$name: $verb, key $key${others:+ (kept: $others)}"
    run "${K[@]}" "$verb" -f "$manifest" >/dev/null
}

log ""
log "==> 5. Secrets"
DEPLOY_EXISTED=""
"${K[@]}" get deploy "$DEPLOY" >/dev/null 2>&1 && DEPLOY_EXISTED=1
apply_secret_key "$INSTANCES_SECRET" "$INSTANCE_KEY" "$INSTANCE_FILE"

HELM_SET=()
[ -n "$IMAGE_TAG" ] && HELM_SET+=(--set-string "image.tag=$IMAGE_TAG")
if [ "$IBAC_JUDGE" = true ]; then
    # credfile's reader: mode-checked, trailing newline stripped — a newline in the key would ride
    # into every judge call's Authorization header.
    # shellcheck source=reference/credfile.sh
    . "$REFERENCE_DIR/credfile.sh"
    ( umask 077; cred_read_file "$IBAC_JUDGE_KEY_FILE" > "$TMP/judge.key" ) \
        || die "could not read IBAC_JUDGE_KEY_FILE"
    [ -s "$TMP/judge.key" ] || die "IBAC_JUDGE_KEY_FILE is empty"
    apply_secret_key "$JUDGE_SECRET" apikey "$TMP/judge.key"
    HELM_SET+=(--set ibacJudge.enabled=true
               --set-string "ibacJudge.upstreamBase=$IBAC_JUDGE_UPSTREAM_BASE"
               --set-string "ibacJudge.model=$IBAC_JUDGE_MODEL")
elif [ -n "$DROP_JUDGE" ]; then
    HELM_SET+=(--set ibacJudge.enabled=false)
fi

# --- 6. helm -----------------------------------------------------------------------------------------
log ""
log "==> 6. helm upgrade --install"
run helm upgrade --install "$RELEASE" "$CHART" "${H[@]}" -f "$HELM_VALUES" ${HELM_SET[@]+"${HELM_SET[@]}"} \
    --wait --timeout 5m

# --- 7. restart --------------------------------------------------------------------------------------
if [ -n "$DEPLOY_EXISTED" ]; then
    log ""
    log "==> 7. restart — the Deployment pre-existed, and the Service reads its Secret only at start"
    run "${K[@]}" rollout restart "deploy/$DEPLOY"
    run "${K[@]}" rollout status "deploy/$DEPLOY" --timeout=180s
fi

if [ -n "$DRY_RUN" ]; then
    log ""
    log "Dry run complete: the checks passed and the commands above are what a real run executes."
    exit 0
fi

# --- 8. verify ---------------------------------------------------------------------------------------
log ""
log "==> 8. verify"
if [ "$AB_PLATFORM" = openshift ]; then
    HOST="$("${K[@]}" get route autobench -o jsonpath='{.spec.host}')"
    BASE="https://$HOST"
else
    BASE="http://$(helm get values "$RELEASE" "${H[@]}" -a -o json | jq -r '.httpRoute.hostname'):8080"
fi
curl -fsS --max-time 20 "$BASE/healthz" >/dev/null || die "$BASE/healthz did not answer 200"
log "    ok    $BASE/healthz"

# Read the s3 block BACK from the live Secret: an instance field an older image does not know is
# dropped silently, and a stale Secret looks exactly like a fresh one from the outside.
"${K[@]}" get secret "$INSTANCES_SECRET" -o json \
    | KEY="$INSTANCE_KEY" jq -r '.data[env.KEY] // empty' | base64 -d > "$TMP/live.json"
LIVE_BUCKET="$(jq -r '.s3.bucket // empty' "$TMP/live.json")"
if [ "$S3_ENABLED" = true ]; then
    [ "$LIVE_BUCKET" = "$S3_BUCKET" ] || die "the live Secret's s3.bucket is '${LIVE_BUCKET:-none}', not $S3_BUCKET"
    log "    ok    s3 publishing on: bucket $LIVE_BUCKET prefix $(jq -r '.s3.prefix // ""' "$TMP/live.json") key sha8 $(jq -j '.s3.access_key_id // ""' "$TMP/live.json" | shasum -a 256 | cut -c1-8)"
else
    [ -z "$LIVE_BUCKET" ] || die "S3_ENABLED=false but the live Secret still names bucket $LIVE_BUCKET"
    log "    ok    s3 publishing off (declared) — runs will publish no artifacts"
fi

log ""
log "==> post-install preflight (with the MLflow round trip, through the Service)"
PF=(--platform "$AB_PLATFORM" --context "$KUBE_CONTEXT" --namespace "$NAMESPACE" --values "$HELM_VALUES"
    --chart "$CHART")
[ "$IBAC_JUDGE" = true ] && PF+=(--plugin-legs)
python3 "$REFERENCE_DIR/preflight.py" "${PF[@]}" \
    || die "installed, but the post-install preflight reports failures (see above)"

log ""
log "Installed. Only a 1-task leg with a non-zero token row proves the whole chain:"
log "  BM_BASE=$BASE BM_ISS=<the iss above> BM_USER=benchmarker BM_PASSWORD_FILE=<your password file> \\"
log "    uv run autobench-cli all --benchmark gsm8k --tasks 1 --teardown"
