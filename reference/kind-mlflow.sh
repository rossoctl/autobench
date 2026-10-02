#!/usr/bin/env bash
# KinD only: install the MLflow read path for AutoBench, and remove it again ONLY if this installed it.
#
#   reference/kind-mlflow.sh plan      --context kind-rossoctl    # prints: install | owned | reuse | skip
#   reference/kind-mlflow.sh install   --context kind-rossoctl [--dry-run]
#   reference/kind-mlflow.sh uninstall --context kind-rossoctl [--dry-run]
#
# Called by autobench-install.sh and autobench-uninstall.sh; safe to run by hand.
#
# The read path has two halves that must point at the same place: deploy/kind/mlflow-reader.yaml (a
# no-auth MLflow over the platform's postgres) and the collector's MLflow exporter, which rossoctl-deps
# points at the OIDC-gated `mlflow` — that one 401s every span export while the run still passes.
#
# Ownership is a RECORD, never a guess: install writes cm/autobench-mlflow-install (rossoctl-system)
# holding the collector's config as it was before, and uninstall acts only when that record exists.
# A reader that was there first — applied by hand, or by kind-post-setup.sh — has no record and is
# left in place, collector and all.
#
#   INSTALL_MLFLOW  auto (default)  install unless a Service already serves MLFLOW_URL
#                   always          install even then — re-applies the manifest and TAKES OWNERSHIP,
#                                   so a later uninstall removes it
#                   never           touch nothing
#   MLFLOW_URL      default http://mlflow-reader.rossoctl-system.svc.cluster.local:5000
#
# Uninstall puts the collector config back only if it is still exactly what install left. If someone
# changed it since, it is left alone and the recorded original is saved to a file instead.
# The traces themselves live in the platform's postgres, so removing the reader deletes no data.

set -euo pipefail
set +x

REFERENCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$REFERENCE_DIR")"
MANIFEST="$REPO_DIR/deploy/kind/mlflow-reader.yaml"
NS="rossoctl-system"
RECORD_CM="autobench-mlflow-install"
COLLECTOR_CM="otel-collector-config"
COLLECTOR_DEPLOY="otel-collector"

log()  { printf '%s\n' "$*" >&2; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'Error: %s\n' "$*" >&2; exit 1; }

CMD="${1:-}"; [ $# -gt 0 ] && shift
KUBE_CONTEXT="${KUBE_CONTEXT:-}"
INSTALL_MLFLOW="${INSTALL_MLFLOW:-auto}"
MLFLOW_URL="${MLFLOW_URL:-http://mlflow-reader.$NS.svc.cluster.local:5000}"
DRY_RUN=""
RECORD_DIR="${RECORD_DIR:-}"
while [ $# -gt 0 ]; do
    case "$1" in
        --context)    KUBE_CONTEXT="${2:-}"; shift 2 ;;
        --mode)       INSTALL_MLFLOW="${2:-}"; shift 2 ;;
        --mlflow-url) MLFLOW_URL="${2:-}"; shift 2 ;;
        --record-dir) RECORD_DIR="${2:-}"; shift 2 ;;
        --dry-run)    DRY_RUN=1; shift ;;
        -h|--help)    sed -n '2,27p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done
case "$CMD" in plan|install|uninstall) ;; *) die "usage: kind-mlflow.sh plan|install|uninstall --context CTX [--dry-run]" ;; esac
[ -n "$KUBE_CONTEXT" ] || die "no context — pass --context or set KUBE_CONTEXT"
case "$INSTALL_MLFLOW" in auto|always|never) ;; *) die "INSTALL_MLFLOW must be auto, always or never (got '$INSTALL_MLFLOW')" ;; esac

K=(kubectl --context "$KUBE_CONTEXT" -n "$NS")
run() { if [ -n "$DRY_RUN" ]; then printf '[dry-run] %s\n' "$*" >&2; else "$@"; fi; }

# The Service named by MLFLOW_URL — the same parse kind-post-setup.sh uses.
HOSTPORT="${MLFLOW_URL#*://}"; HOSTPORT="${HOSTPORT%%/*}"
URL_SVC="${HOSTPORT%%.*}"; URL_SVC="${URL_SVC%%:*}"
URL_NS="${HOSTPORT#*.}"; URL_NS="${URL_NS%%.*}"
[ "$URL_NS" = "${HOSTPORT%%.*}" ] && URL_NS="$NS"

have_record() { "${K[@]}" get cm "$RECORD_CM" >/dev/null 2>&1; }
# sha8 of a ConfigMap's .data, key order normalised — what "unchanged since" is judged by.
data_sha() { jq -cS '.data // {}' | shasum -a 256 | cut -c1-8; }

plan() {
    if have_record; then echo owned; return; fi
    case "$INSTALL_MLFLOW" in
        never)  echo skip ;;
        always) echo install ;;
        auto)   if kubectl --context "$KUBE_CONTEXT" -n "$URL_NS" get svc "$URL_SVC" >/dev/null 2>&1; then
                    echo reuse
                else
                    echo install
                fi ;;
    esac
}

# A python that can import yaml: the collector's config is one embedded YAML document, and
# kind-collector-mlflow.py edits it in place. The user's python3 often has no PyYAML; the repo's uv
# environment does.
yaml_python() {
    if python3 -c 'import yaml' >/dev/null 2>&1; then
        PY=(python3)
    elif command -v uv >/dev/null 2>&1 \
        && uv run --project "$REPO_DIR" --quiet python -c 'import yaml' >/dev/null 2>&1; then
        PY=(uv run --project "$REPO_DIR" --quiet python)
    else
        die "repointing the collector needs PyYAML: python3 has none and uv is not available — pip install pyyaml, or install uv"
    fi
}

wait_reader() {
    "${K[@]}" rollout status deploy/mlflow-reader --timeout=300s >&2
    # Gate on the API, not on Ready: two pip installs run at container start. The probe runs INSIDE
    # the pod (MLflow 3.x rejects the API server's proxy), names an experiment (else 400), and uses
    # python because the image ships no curl.
    local probe="import urllib.request;urllib.request.urlopen('http://localhost:5000/api/2.0/mlflow/traces?experiment_ids=0&max_results=1',timeout=10)"
    for _ in $(seq 1 36); do
        if "${K[@]}" exec deploy/mlflow-reader -- python -c "$probe" >/dev/null 2>&1; then
            log "    ok    mlflow-reader answers /api/2.0/mlflow/traces"
            return 0
        fi
        sleep 5
    done
    die "mlflow-reader is Ready but its traces API never answered — check the container's lastState (one MLflow 3.x worker idles at ~2.3 GiB; an OOMKill leaves a log ending on 'Application startup complete')"
}

do_install() {
    local p; p="$(plan)"
    case "$p" in
        skip)  log "    MLflow: INSTALL_MLFLOW=never — nothing installed; the collector and $MLFLOW_URL are as found"; return 0 ;;
        reuse) log "    MLflow: svc/$URL_SVC in $URL_NS already serves $MLFLOW_URL and was NOT installed by this script"
               log "            (no cm/$RECORD_CM) — reused as is, and uninstall will leave it; INSTALL_MLFLOW=always takes it over"
               return 0 ;;
    esac
    [ "$URL_SVC" = mlflow-reader ] && [ "$URL_NS" = "$NS" ] \
        || die "MLFLOW_URL names svc/$URL_SVC in $URL_NS, but $MANIFEST serves mlflow-reader in $NS — installing it would not make that URL answer"
    yaml_python

    local cur; cur="$("${K[@]}" get cm "$COLLECTOR_CM" -o json)" || die "no cm/$COLLECTOR_CM in $NS — is this a rossoctl-deps cluster?"
    if [ "$p" = owned ]; then
        log "    MLflow: owned by this script (cm/$RECORD_CM) — re-applying, collector original stays as recorded"
    else
        log "    MLflow: installing mlflow-reader and recording ownership in cm/$RECORD_CM"
        # The record goes in FIRST, so an install that dies half way is still undone by uninstall.
        # It holds the collector's config verbatim — its credentials are ${env:...} references
        # resolved from the Deployment's env, so the copy carries no secret.
        local rec="$TMP/record.json"
        printf '%s' "$cur" | NS="$NS" NAME="$RECORD_CM" jq \
            --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --arg sha "$(printf '%s' "$cur" | data_sha)" \
            '{apiVersion: "v1", kind: "ConfigMap",
              metadata: {name: env.NAME, namespace: env.NS,
                         labels: {"app.kubernetes.io/managed-by": "autobench-install.sh"}},
              data: {owner: "autobench-install.sh", installedAt: $at,
                     readerManifest: "deploy/kind/mlflow-reader.yaml",
                     collectorPriorSha: $sha, collectorPrior: (.data // {} | tojson)}}' > "$rec"
        run "${K[@]}" create -f "$rec" >/dev/null
    fi
    run kubectl --context "$KUBE_CONTEXT" apply -f "$MANIFEST" >&2
    [ -n "$DRY_RUN" ] || wait_reader
    run "${PY[@]}" "$REFERENCE_DIR/kind-collector-mlflow.py" --context "$KUBE_CONTEXT" \
        --reader-url "${MLFLOW_URL%/}/v1/traces" >&2
    if [ -z "$DRY_RUN" ]; then
        local post; post="$("${K[@]}" get cm "$COLLECTOR_CM" -o json | data_sha)"
        "${K[@]}" patch cm "$RECORD_CM" --type merge -p "{\"data\":{\"collectorPostSha\":\"$post\"}}" >/dev/null
        log "    ok    collector exports to ${MLFLOW_URL%/}/v1/traces (config sha8 $post; original recorded)"
    fi
}

do_uninstall() {
    if ! have_record; then
        if "${K[@]}" get deploy mlflow-reader >/dev/null 2>&1; then
            log "    kept    deploy/mlflow-reader — not installed by autobench-install.sh (no cm/$RECORD_CM); left in place with the collector"
        else
            log "    absent  no mlflow-reader and no cm/$RECORD_CM — nothing to remove"
        fi
        return 0
    fi
    local rec cur prior_sha post_sha cur_sha
    rec="$("${K[@]}" get cm "$RECORD_CM" -o json)"
    prior_sha="$(jq -r '.data.collectorPriorSha // ""' <<<"$rec")"
    post_sha="$(jq -r '.data.collectorPostSha // ""' <<<"$rec")"
    cur="$("${K[@]}" get cm "$COLLECTOR_CM" -o json 2>/dev/null || true)"
    log "    record  cm/$RECORD_CM — installed $(jq -r '.data.installedAt' <<<"$rec") by $(jq -r '.data.owner' <<<"$rec")"

    if [ -z "$cur" ]; then
        warn "cm/$COLLECTOR_CM is gone — nothing to restore"
    else
        cur_sha="$(printf '%s' "$cur" | data_sha)"
        if [ "$cur_sha" = "$prior_sha" ]; then
            log "    ok    collector config is already the recorded original ($cur_sha) — not touched"
        elif [ -n "$post_sha" ] && [ "$cur_sha" = "$post_sha" ]; then
            # Back to exactly what it was. The last-applied annotation the repoint's `kubectl apply`
            # added goes too: the chart-made object had none.
            jq --argjson d "$(jq -r '.data.collectorPrior' <<<"$rec")" \
                '.data = $d | del(.metadata.annotations["kubectl.kubernetes.io/last-applied-configuration"])' \
                <<<"$cur" > "$TMP/collector.json"
            run "${K[@]}" replace -f "$TMP/collector.json" >/dev/null
            run "${K[@]}" rollout restart "deploy/$COLLECTOR_DEPLOY" >/dev/null
            run "${K[@]}" rollout status "deploy/$COLLECTOR_DEPLOY" --timeout=180s >&2
            log "    ok    collector config restored to the recorded original ($cur_sha -> $prior_sha) and restarted"
        else
            local keep="${RECORD_DIR:-$TMP}/otel-collector-config.prior.json"
            jq -r '.data.collectorPrior' <<<"$rec" > "$keep"
            warn "cm/$COLLECTOR_CM changed since the install ($cur_sha, neither the original $prior_sha nor what install left ${post_sha:-?}) — NOT restored; the original is in $keep"
        fi
    fi

    run kubectl --context "$KUBE_CONTEXT" delete -f "$MANIFEST" --ignore-not-found --wait=true >&2
    if [ -z "$DRY_RUN" ]; then
        left="$(kubectl --context "$KUBE_CONTEXT" get -f "$MANIFEST" --ignore-not-found -o name 2>/dev/null || true)"
        [ -z "$left" ] || die "still present after delete: $(printf '%s' "$left" | tr '\n' ' ')"
        log "    ok    mlflow-reader Service + Deployment are NotFound (the traces stay in postgres)"
    fi
    run "${K[@]}" delete cm "$RECORD_CM" >/dev/null
    [ -n "$DRY_RUN" ] || log "    ok    cm/$RECORD_CM deleted"
}

TMP="$(umask 077; mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
case "$CMD" in
    plan)      plan ;;
    install)   do_install ;;
    uninstall) do_uninstall ;;
esac
