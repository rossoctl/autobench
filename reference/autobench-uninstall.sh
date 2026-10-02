#!/usr/bin/env bash
# Uninstall AutoBench from one cluster — workloads first, then the release — and prove it is gone.
#
#   reference/autobench-uninstall.sh --env-file ~/.rossoctl-<cluster>/autobench.env
#   reference/autobench-uninstall.sh --platform kind --context kind-rossoctl --dry-run
#
# Same inputs as autobench-install.sh (reference/autobench.env.template): shell < --env-file(s) <
# flags. It needs far fewer of them — the platform, the context, and the benchmarker password the
# workload teardown logs in with. S3 is not consulted.
#
#   1. workloads    every exgentic-* AgentRuntime in the team namespaces, deleted THROUGH THE SERVICE
#                   by exact name (autobench-cli delete-agent / delete-tool), then re-listed until
#                   none remain. Not `autobench-cli teardown`: DELETE /benchmarks/{b}/deploy defaults
#                   experiment=default, returns 204 having deleted nothing for a named experiment,
#                   and stops at a missing agent before reaching its MCP tool.
#   2. record       `helm get manifest` and `helm get hooks` saved under /tmp — the authority on what
#                   the release owns, and the only list left once it is gone
#   3. uninstall    helm uninstall --wait. If the judge's pre-delete hook fails it STOPS and names the
#                   Job's logs: --no-hooks would leave rossoctl-platform-config naming a dead judge
#   4. verify       every object in the saved manifest is NotFound; the ibac.* fields no longer
#                   name the removed judge
#   5. Secrets      autobench-instances and ibac-judge-upstream are made out-of-band and so are KEPT
#                   and reported; --purge-secrets deletes them too
#   6. MLflow       kind only: reference/kind-mlflow.sh uninstall removes mlflow-reader and puts the
#                   collector's config back — ONLY if autobench-install.sh installed them, which its
#                   record cm/autobench-mlflow-install says. A reader made any other way is kept.
#                   --keep-mlflow skips it
#
# Workloads that are not AutoBench's (anything not named exgentic-*) are never touched.

set -euo pipefail
set +x   # never trace: the environment carries credentials

REFERENCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$REFERENCE_DIR")"

log()  { printf '%s\n' "$*" >&2; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'Error: %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: reference/autobench-uninstall.sh [--env-file FILE]... [flags]

  --env-file FILE     load KEY=VALUE pairs (repeatable; later files win, flags beat files)
  --platform P        AB_PLATFORM     openshift | kind                                  (required)
  --context CTX       KUBE_CONTEXT    kubectl context of the Service's cluster          (required)
  --teams LIST        TEAMS           team namespaces to clear of exgentic-* workloads (default team1)
  --keep-workloads                    skip step 1 (they keep running, and keep their old config)
  --purge-secrets                     also delete autobench-instances and ibac-judge-upstream
  --keep-mlflow                       kind: keep an MLflow read path autobench-install.sh installed
  --dry-run                           list what would go; delete nothing
  -h, --help

The workload teardown logs in as KC_SERVICE_USERNAME (default benchmarker) with KC_SERVICE_PASSWORD
or KC_SERVICE_PASSWORD_FILE — or BM_USER / BM_PASSWORD / BM_PASSWORD_FILE, which win. BM_BASE
defaults to the Service's Route/HTTPRoute and BM_ISS to the iss in the instance Secret.
EOF
}

# shellcheck source=reference/envfile.sh
. "$REFERENCE_DIR/envfile.sh"
envfile_prescan "$@" || exit 1

AB_PLATFORM="${AB_PLATFORM:-}"
KUBE_CONTEXT="${KUBE_CONTEXT:-}"
TEAMS="${TEAMS:-team1}"
NAMESPACE="${NAMESPACE:-rossoctl-system}"
RELEASE="autobench"
INSTANCES_SECRET="autobench-instances"
JUDGE_SECRET="ibac-judge-upstream"
DEPLOY="autobench-service"
KEEP_WORKLOADS=""
PURGE_SECRETS=""
KEEP_MLFLOW=""
DRY_RUN=""

while [ $# -gt 0 ]; do
    case "$1" in
        --env-file)       shift 2 ;;
        --env-file=*)     shift ;;
        --platform)       AB_PLATFORM="${2:-}"; shift 2 ;;
        --context)        KUBE_CONTEXT="${2:-}"; shift 2 ;;
        --teams)          TEAMS="${2:-}"; shift 2 ;;
        --keep-workloads) KEEP_WORKLOADS=1; shift ;;
        --purge-secrets)  PURGE_SECRETS=1; shift ;;
        --keep-mlflow)    KEEP_MLFLOW=1; shift ;;
        --dry-run)        DRY_RUN=1; shift ;;
        -h|--help)        usage; exit 0 ;;
        *) usage >&2; die "unknown argument: $1" ;;
    esac
done

[ -d "$HOME/.rd/bin" ] && PATH="$PATH:$HOME/.rd/bin"
missing=()
for t in kubectl helm jq uv; do command -v "$t" >/dev/null 2>&1 || missing+=("$t"); done
[ ${#missing[@]} -eq 0 ] || die "not on PATH: ${missing[*]}"
# The workload teardown runs autobench-cli, which needs the package and Python >= 3.11: the repo's
# uv environment, never the python3 on PATH (reference/pyrun.sh).
# shellcheck source=reference/pyrun.sh
. "$REFERENCE_DIR/pyrun.sh"
repo_python "$REPO_DIR" || die "$REPO_PYTHON_HINT"

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

K=(kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE")
H=(--kube-context "$KUBE_CONTEXT" -n "$NAMESPACE")
# The saved manifest is read with NO -n: every object carries its own namespace, and one is not the
# Service's — the MLflow trace-writer RoleBinding lives in the MLflow workspace namespace.
TS="$(date +%Y%m%d-%H%M%S)"
SAFE_CTX="$(printf '%s' "$KUBE_CONTEXT" | tr -c 'A-Za-z0-9._-' '_')"
RECORD="/tmp/autobench-uninstall-$SAFE_CTX-$TS"
TMP="$(umask 077; mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

run() {
    if [ -n "$DRY_RUN" ]; then printf '[dry-run] %s\n' "$*" >&2; else "$@"; fi
}

log "AutoBench uninstall — platform=$AB_PLATFORM context=$KUBE_CONTEXT namespace=$NAMESPACE teams=$TEAMS"
[ -n "${ENVFILE_LOADED:-}" ] && log "  env files: $ENVFILE_LOADED"
[ -n "$DRY_RUN" ] && log "  DRY RUN — nothing is deleted"

HAVE_RELEASE=""
helm status "$RELEASE" "${H[@]}" >/dev/null 2>&1 && HAVE_RELEASE=1

# exgentic-* AgentRuntimes in one namespace, one name per line.
list_workloads() {  # $1 namespace
    kubectl --context "$KUBE_CONTEXT" -n "$1" get agentruntimes -o json 2>/dev/null \
        | jq -r '.items[].metadata.name | select(startswith("exgentic-"))'
}

# --- 1. workloads, through the Service ----------------------------------------------------------------
log ""
log "==> 1. workloads"
TEAM_LIST="${TEAMS//,/ }"
TOTAL=0
for t in $TEAM_LIST; do
    n="$(list_workloads "$t" | grep -c . || true)"
    TOTAL=$((TOTAL + n))
    [ "$n" -gt 0 ] && list_workloads "$t" | sed "s|^|    $t/|" >&2
done
if [ "$TOTAL" -eq 0 ]; then
    log "    none — no exgentic-* AgentRuntime in: $TEAMS"
elif [ -n "$KEEP_WORKLOADS" ]; then
    warn "--keep-workloads — $TOTAL workload(s) keep running, and a pod that is already running is never re-pulled or re-configured"
else
    "${K[@]}" get deploy "$DEPLOY" >/dev/null 2>&1 \
        || die "$TOTAL workload(s) remain but deploy/$DEPLOY is gone, so they cannot be torn down through the Service — reinstall it, or pass --keep-workloads and delete them through Rossoctl"
    if [ -z "${BM_BASE:-}" ]; then
        if [ "$AB_PLATFORM" = openshift ]; then
            BM_BASE="https://$("${K[@]}" get route autobench -o jsonpath='{.spec.host}')"
        else
            BM_BASE="http://$(helm get values "$RELEASE" "${H[@]}" -a -o json | jq -r '.httpRoute.hostname'):8080"
        fi
    fi
    if [ -z "${BM_ISS:-}" ]; then
        # iss is an identity string, not a credential — but read it without printing the rest.
        "${K[@]}" get secret "$INSTANCES_SECRET" -o json \
            | jq -r '.data | to_entries[] | .value' > "$TMP/instances.b64"
        ISSES="$(while IFS= read -r b; do printf '%s' "$b" | base64 -d | jq -r '.iss // empty'; done < "$TMP/instances.b64" | sort -u)"
        [ "$(printf '%s\n' "$ISSES" | grep -c .)" -eq 1 ] \
            || die "secret/$INSTANCES_SECRET holds $(printf '%s\n' "$ISSES" | grep -c .) distinct iss values — set BM_ISS"
        BM_ISS="$ISSES"
    fi
    BM_USER="${BM_USER:-${KC_SERVICE_USERNAME:-benchmarker}}"
    if [ -z "${BM_PASSWORD:-}" ] && [ -z "${BM_PASSWORD_FILE:-}" ]; then
        [ -n "${KC_SERVICE_PASSWORD:-}" ] && [ -n "${KC_SERVICE_PASSWORD_FILE:-}" ] \
            && die "both KC_SERVICE_PASSWORD and KC_SERVICE_PASSWORD_FILE are set — set one"
        BM_PASSWORD="${KC_SERVICE_PASSWORD:-}"
        BM_PASSWORD_FILE="${KC_SERVICE_PASSWORD_FILE:-}"
        [ -n "$BM_PASSWORD$BM_PASSWORD_FILE" ] \
            || die "no benchmarker password — set KC_SERVICE_PASSWORD or KC_SERVICE_PASSWORD_FILE (or BM_PASSWORD[_FILE])"
    fi
    export BM_BASE BM_ISS BM_USER BM_PASSWORD BM_PASSWORD_FILE
    [ -n "${BM_PASSWORD:-}" ] || unset BM_PASSWORD
    [ -n "${BM_PASSWORD_FILE:-}" ] || unset BM_PASSWORD_FILE
    log "    via $BM_BASE as $BM_USER (iss $BM_ISS)"
    CLI=("${PY[@]}" -m autobench.cli)

    for t in $TEAM_LIST; do
        # Agents before tools, so nothing is left calling an MCP that is already gone.
        for w in $(list_workloads "$t" | grep '^exgentic-a2a-' || true); do
            run "${CLI[@]}" delete-agent --namespace "$t" --name "$w" || die "could not delete $t/$w"
        done
        for w in $(list_workloads "$t" | grep -v '^exgentic-a2a-' || true); do
            run "${CLI[@]}" delete-tool --namespace "$t" --name "$w" || die "could not delete $t/$w"
        done
    done
    if [ -z "$DRY_RUN" ]; then
        # A 204 is a request, not a result: re-list until nothing remains.
        for _ in $(seq 1 24); do
            left=0
            for t in $TEAM_LIST; do left=$((left + $(list_workloads "$t" | grep -c . || true))); done
            [ "$left" -eq 0 ] && break
            sleep 5
        done
        if [ "$left" -ne 0 ]; then
            for t in $TEAM_LIST; do list_workloads "$t" | sed "s|^|    still present: $t/|" >&2; done
            die "$left workload(s) survived the teardown — the release was NOT uninstalled"
        fi
        log "    ok    no exgentic-* AgentRuntime remains"
    fi
fi

# --- 2-4. the release ---------------------------------------------------------------------------------
if [ -z "$HAVE_RELEASE" ]; then
    log ""
    log "==> 2-4. no Helm release '$RELEASE' in $NAMESPACE — nothing to uninstall"
    if "${K[@]}" get deploy "$DEPLOY" >/dev/null 2>&1; then
        warn "deploy/$DEPLOY exists without a release — it came from the raw manifests (ADMIN_GUIDE §5.3); not touched"
    fi
else
    log ""
    log "==> 2. record what the release owns"
    mkdir -p "$RECORD"
    helm get manifest "$RELEASE" "${H[@]}" > "$RECORD/manifest.yaml"
    helm get hooks "$RELEASE" "${H[@]}" > "$RECORD/hooks.yaml" || true
    JUDGE_ON="$(helm get values "$RELEASE" "${H[@]}" -o json | jq -r '.ibacJudge.enabled // false')"
    log "    $RECORD/manifest.yaml ($(grep -c '^kind:' "$RECORD/manifest.yaml" || true) objects; judge $JUDGE_ON)"
    kubectl --context "$KUBE_CONTEXT" get -f "$RECORD/manifest.yaml" -o name 2>/dev/null | sed 's/^/    /' >&2 || true

    log ""
    log "==> 3. helm uninstall"
    if ! run helm uninstall "$RELEASE" "${H[@]}" --wait --timeout 5m; then
        log ""
        log "The uninstall failed. If it was the IBAC judge's pre-delete hook, its log says why:"
        log "  kubectl --context '$KUBE_CONTEXT' -n $NAMESPACE logs job/ibac-judge-config-restore"
        log "Fix that and re-run. Do NOT reach for --no-hooks: it skips the restore and leaves"
        log "rossoctl-platform-config naming a judge that no longer exists."
        exit 1
    fi

    if [ -z "$DRY_RUN" ]; then
        log ""
        log "==> 4. verify"
        left="$(kubectl --context "$KUBE_CONTEXT" get -f "$RECORD/manifest.yaml" --ignore-not-found -o name 2>/dev/null || true)"
        if [ -n "$left" ]; then
            printf '%s\n' "$left" | sed 's/^/    still present: /' >&2
            die "objects of the uninstalled release are still present"
        fi
        log "    ok    every object in the saved manifest is NotFound"
        hooks_left="$(kubectl --context "$KUBE_CONTEXT" get -f "$RECORD/hooks.yaml" --ignore-not-found -o name 2>/dev/null || true)"
        [ -z "$hooks_left" ] || warn "hook objects left behind (harmless, but untidy): $(printf '%s' "$hooks_left" | tr '\n' ' ')"
        # The two ibac.* fields live in another release's ConfigMap; the pre-delete hook restores them.
        cfg="$("${K[@]}" get configmap rossoctl-platform-config -o json 2>/dev/null | jq -r '.data["config.yaml"] // ""' || true)"
        if case "$cfg" in *ibac-judge.*) true ;; *) false ;; esac; then
            die "rossoctl-platform-config still names the removed judge — the restore hook did not run"
        fi
        [ "$JUDGE_ON" = true ] && log "    ok    rossoctl-platform-config no longer names the judge"
    fi
fi

# --- 5. the out-of-band Secrets ------------------------------------------------------------------------
log ""
log "==> 5. out-of-band Secrets"
for s in "$INSTANCES_SECRET" "$JUDGE_SECRET"; do
    if "${K[@]}" get secret "$s" >/dev/null 2>&1; then
        keys="$("${K[@]}" get secret "$s" -o json | jq -r '.data // {} | keys | join(", ")')"
        if [ -n "$PURGE_SECRETS" ]; then
            run "${K[@]}" delete secret "$s"
            log "    deleted secret/$s (keys: $keys)"
        else
            log "    kept    secret/$s (keys: $keys) — --purge-secrets deletes it"
        fi
    else
        log "    absent  secret/$s"
    fi
done

# --- 6. the MLflow read path (kind) --------------------------------------------------------------------
if [ "$AB_PLATFORM" = kind ]; then
    log ""
    log "==> 6. MLflow read path"
    if [ -n "$KEEP_MLFLOW" ]; then
        log "    kept    --keep-mlflow — mlflow-reader and the collector config stay as they are"
    else
        mkdir -p "$RECORD"
        "$REFERENCE_DIR/kind-mlflow.sh" uninstall --context "$KUBE_CONTEXT" --record-dir "$RECORD" \
            ${DRY_RUN:+--dry-run} || die "the MLflow removal failed (see above)"
    fi
fi

log ""
if [ -n "$DRY_RUN" ]; then
    log "Dry run complete: nothing was deleted."
else
    log "Uninstalled.${HAVE_RELEASE:+ The release record is in $RECORD.}"
fi
