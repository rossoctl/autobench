#!/usr/bin/env bash
# OpenShift only: create the MLflow credential the Service uses by default — sa/mlflow-reader and its
# token Secret — and remove them again ONLY if this created them.
#
#   reference/ocp-mlflow.sh plan      --context CTX    # prints: install | owned | reuse | skip
#   reference/ocp-mlflow.sh install   --context CTX [--dry-run]
#   reference/ocp-mlflow.sh uninstall --context CTX [--dry-run]
#
# Called by autobench-install.sh (step 3b) and autobench-uninstall.sh (step 6); safe to run by hand.
#
# OpenShift's MLflow is pre-installed and SAR-gated, and the Service talks to it with a ServiceAccount
# bearer: ocp-service-bootstrap.sh copies `.data.token` of MLFLOW_TOKEN_SECRET into the instance file,
# and the chart binds the same account (mlflowTraceWriter.serviceAccount) so the Service may also
# WRITE its spans. The chart references that account and never creates it, so it used to be made by
# hand once per cluster — and no uninstall could tell whether it was safe to remove.
#
# Ownership is a RECORD, never a guess, as in kind-mlflow.sh: install writes
# cm/autobench-mlflow-credential (rossoctl-system) BEFORE it creates anything, naming each object it
# owns and, once that exists, its uid. Uninstall deletes only recorded objects whose uid still
# matches — one deleted and re-made by someone else since is theirs. An account that was there first
# has no record and is left in place.
#
#   INSTALL_MLFLOW  auto (default)  create whichever of the two is missing, and own only that
#                   always          also TAKE OWNERSHIP of the ones already there, so a later
#                                   uninstall removes them
#                   never           touch nothing
#   MLFLOW_SA            default mlflow-reader — must equal the chart's mlflowTraceWriter.serviceAccount
#   MLFLOW_TOKEN_SECRET  default mlflow-reader-token — what ocp-service-bootstrap.sh reads
#
# Nothing is created when the install declares another credential shape (MLFLOW_NO_AUTH,
# MLFLOW_BEARER_FILE, MLFLOW_USERNAME/PASSWORD_FILE, MLFLOW_CLIENT_ID/CLIENT_SECRET_FILE/TOKEN_URL):
# the token Secret is then not what the Service authenticates with.
#
# Uninstall refuses while the Helm release exists or a pod runs as the account, and never deletes a
# RoleBinding: one made by hand that still names the account is reported, not touched.

set -euo pipefail
set +x   # the token Secret is read; nothing here prints a value

NS="rossoctl-system"
RECORD_CM="autobench-mlflow-credential"
RELEASE="autobench"

log()  { printf '%s\n' "$*" >&2; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'Error: %s\n' "$*" >&2; exit 1; }

CMD="${1:-}"; [ $# -gt 0 ] && shift
KUBE_CONTEXT="${KUBE_CONTEXT:-}"
INSTALL_MLFLOW="${INSTALL_MLFLOW:-auto}"
SA="${MLFLOW_SA:-mlflow-reader}"
TOKEN_SECRET="${MLFLOW_TOKEN_SECRET:-mlflow-reader-token}"
DRY_RUN=""
while [ $# -gt 0 ]; do
    case "$1" in
        --context)    KUBE_CONTEXT="${2:-}"; shift 2 ;;
        --mode)       INSTALL_MLFLOW="${2:-}"; shift 2 ;;
        --dry-run)    DRY_RUN=1; shift ;;
        -h|--help)    sed -n '2,35p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done
case "$CMD" in plan|install|uninstall) ;; *) die "usage: ocp-mlflow.sh plan|install|uninstall --context CTX [--dry-run]" ;; esac
[ -n "$KUBE_CONTEXT" ] || die "no context — pass --context or set KUBE_CONTEXT"
case "$INSTALL_MLFLOW" in auto|always|never) ;; *) die "INSTALL_MLFLOW must be auto, always or never (got '$INSTALL_MLFLOW')" ;; esac

K=(kubectl --context "$KUBE_CONTEXT" -n "$NS")
run() { if [ -n "$DRY_RUN" ]; then printf '[dry-run] %s\n' "$*" >&2; else "$@"; fi; }

obj_name() { case "$1" in serviceaccount) printf '%s' "$SA" ;; secret) printf '%s' "$TOKEN_SECRET" ;; esac; }
uid_of()   { { "${K[@]}" get "$1" "$(obj_name "$1")" -o json 2>/dev/null || echo '{}'; } | jq -r '.metadata.uid // empty'; }
record()   { "${K[@]}" get cm "$RECORD_CM" -o json 2>/dev/null || true; }
# The uid the record owns $1 under: "-" when it does not own it, "" when owned but not yet created.
owned_uid() { jq -r --arg k "owns.$1" 'if (.data // {})[$k] == null then "-" else .data[$k] end' <<<"$REC"; }
own() {  # $1 kind, $2 uid — in the cluster's record and in $REC
    run "${K[@]}" patch cm "$RECORD_CM" --type merge \
        -p "$(jq -nc --arg k "owns.$1" --arg v "$2" '{data: {($k): $v}}')" >/dev/null
    REC="$(jq --arg k "owns.$1" --arg v "$2" '.data[$k] = $v' <<<"$REC")"
}

# The flag that declares another credential shape, if any — the same set ocp-service-bootstrap.sh reads.
other_shape() {
    local v
    for v in MLFLOW_NO_AUTH MLFLOW_BEARER_FILE MLFLOW_USERNAME MLFLOW_PASSWORD_FILE MLFLOW_CLIENT_ID \
             MLFLOW_CLIENT_SECRET_FILE MLFLOW_TOKEN_URL; do
        if [ -n "${!v:-}" ]; then printf '%s' "$v"; return 0; fi
    done
    return 1
}

plan() {
    if other_shape >/dev/null || [ "$INSTALL_MLFLOW" = never ]; then echo skip; return; fi
    if [ -n "$(record)" ]; then echo owned; return; fi
    if [ "$INSTALL_MLFLOW" = always ]; then echo install; return; fi
    if [ -n "$(uid_of serviceaccount)" ] && [ -n "$(uid_of secret)" ]; then echo reuse; else echo install; fi
}

# The token controller fills `.data.token` asynchronously, and only for the account the annotation
# names — a Secret made for another account would hand the Service someone else's identity.
check_token() {
    local s ann
    for _ in $(seq 1 30); do
        s="$("${K[@]}" get secret "$TOKEN_SECRET" -o json 2>/dev/null || true)"
        [ -n "$s" ] || s='{}'
        ann="$(jq -r '.metadata.annotations["kubernetes.io/service-account.name"] // ""' <<<"$s")"
        if [ "$s" != '{}' ] && [ "$ann" != "$SA" ]; then
            die "secret/$TOKEN_SECRET is the token of '${ann:-no account}', not of sa/$SA"
        fi
        if [ -n "$(jq -r '.data.token // empty' <<<"$s")" ]; then
            log "    ok      secret/$TOKEN_SECRET holds a token for sa/$SA"
            return 0
        fi
        sleep 2
    done
    die "secret/$TOKEN_SECRET has no token after 60 s — is it a kubernetes.io/service-account-token Secret, and is the token controller running?"
}

create() {  # $1 kind
    case "$1" in
        serviceaccount)
            run "${K[@]}" create serviceaccount "$SA" >/dev/null ;;
        secret)
            jq -n --arg ns "$NS" --arg name "$TOKEN_SECRET" --arg sa "$SA" \
                '{apiVersion: "v1", kind: "Secret", type: "kubernetes.io/service-account-token",
                  metadata: {name: $name, namespace: $ns,
                             labels: {"app.kubernetes.io/managed-by": "autobench-install.sh"},
                             annotations: {"kubernetes.io/service-account.name": $sa}}}' > "$TMP/token.json"
            run "${K[@]}" create -f "$TMP/token.json" >/dev/null ;;
    esac
}

do_install() {
    local p shape kind name uid owned
    p="$(plan)"
    case "$p" in
        skip)
            if shape="$(other_shape)"; then
                log "    MLflow credential: $shape declares another shape — secret/$TOKEN_SECRET is not what the Service reads with; nothing created"
            else
                log "    MLflow credential: INSTALL_MLFLOW=never — nothing created"
            fi
            return 0 ;;
        reuse)
            log "    MLflow credential: sa/$SA and secret/$TOKEN_SECRET already exist and were NOT created by this script"
            log "            (no cm/$RECORD_CM) — reused as is, and uninstall will leave them; INSTALL_MLFLOW=always takes them over"
            check_token
            return 0 ;;
    esac
    REC="$(record)"
    if [ -n "$REC" ]; then
        log "    MLflow credential: owned by this script (cm/$RECORD_CM)"
    else
        log "    MLflow credential: recording ownership in cm/$RECORD_CM"
        # The record goes in FIRST, so an install that dies half way is still undone by uninstall.
        jq -n --arg ns "$NS" --arg name "$RECORD_CM" --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
              --arg sa "$SA" --arg sec "$TOKEN_SECRET" \
            '{apiVersion: "v1", kind: "ConfigMap",
              metadata: {name: $name, namespace: $ns,
                         labels: {"app.kubernetes.io/managed-by": "autobench-install.sh"}},
              data: {owner: "autobench-install.sh", installedAt: $at,
                     serviceAccount: $sa, tokenSecret: $sec}}' > "$TMP/record.json"
        run "${K[@]}" create -f "$TMP/record.json" >/dev/null
        REC="$(cat "$TMP/record.json")"
    fi
    for kind in serviceaccount secret; do   # the account first: its token is minted for it
        name="$(obj_name "$kind")"
        uid="$(uid_of "$kind")"
        owned="$(owned_uid "$kind")"
        if [ -z "$uid" ]; then
            own "$kind" ""   # recorded before it exists
            create "$kind"
            if [ -z "$DRY_RUN" ]; then
                uid="$(uid_of "$kind")"
                [ -n "$uid" ] || die "$kind/$name was created but cannot be read back"
                own "$kind" "$uid"
            fi
            log "    created $kind/$name"
        elif [ "$owned" = "-" ] && [ "$INSTALL_MLFLOW" = always ]; then
            own "$kind" "$uid"
            log "    took    $kind/$name — INSTALL_MLFLOW=always, so uninstall now removes it"
        elif [ "$owned" = "-" ]; then
            log "    kept    $kind/$name — already there, not created by this script"
        elif [ -z "$owned" ] || [ "$owned" = "$uid" ]; then
            [ -n "$owned" ] || own "$kind" "$uid"   # a run that died between create and record
            log "    ok      $kind/$name (ours)"
        else
            log "    kept    $kind/$name — re-created by someone else since the install (uid differs), not ours"
        fi
    done
    [ -n "$DRY_RUN" ] || check_token
}

do_uninstall() {
    local rel users kind name uid owned deleted_sa="" bindings
    REC="$(record)"
    if [ -z "$REC" ]; then
        local any=""
        for kind in secret serviceaccount; do
            if [ -n "$(uid_of "$kind")" ]; then
                any=1
                log "    kept    $kind/$(obj_name "$kind") — not created by autobench-install.sh (no cm/$RECORD_CM)"
            fi
        done
        [ -n "$any" ] || log "    absent  no sa/$SA, no secret/$TOKEN_SECRET and no cm/$RECORD_CM — nothing to remove"
        return 0
    fi
    # The record names what it made; the environment may since have named something else.
    SA="$(jq -r '.data.serviceAccount' <<<"$REC")"
    TOKEN_SECRET="$(jq -r '.data.tokenSecret' <<<"$REC")"
    log "    record  cm/$RECORD_CM — installed $(jq -r '.data.installedAt' <<<"$REC") by $(jq -r '.data.owner' <<<"$REC")"

    # Names only — a Helm release is a Secret, and its content is not ours to read.
    rel="$("${K[@]}" get secret -l "owner=helm,name=$RELEASE" -o name 2>/dev/null || true)"
    if [ -n "$rel" ]; then
        if [ -n "$DRY_RUN" ]; then
            warn "the Helm release $RELEASE still exists — a real run removes it first, or this step refuses"
        else
            die "the Helm release $RELEASE still exists, and the Service reads MLflow as sa/$SA — uninstall the release first"
        fi
    fi
    if [ "$(owned_uid serviceaccount)" != "-" ]; then
        users="$({ "${K[@]}" get pods -o json 2>/dev/null || echo '{}'; } \
            | jq -r --arg sa "$SA" '.items[]? | select(.spec.serviceAccountName == $sa) | .metadata.name')"
        if [ -n "$users" ]; then
            users="$(printf '%s' "$users" | tr '\n' ' ')"
            if [ -n "$DRY_RUN" ]; then warn "pod(s) still run as sa/$SA: ${users}— a real run refuses"
            else die "pod(s) still run as sa/$SA: ${users}— nothing removed"; fi
        fi
    fi

    for kind in secret serviceaccount; do   # the token before the account it belongs to
        name="$(obj_name "$kind")"
        uid="$(uid_of "$kind")"
        owned="$(owned_uid "$kind")"
        if [ "$owned" = "-" ]; then
            [ -z "$uid" ] || log "    kept    $kind/$name — there before the install, not created by it"
            continue
        fi
        if [ -z "$uid" ]; then
            log "    absent  $kind/$name"
            continue
        fi
        if [ -n "$owned" ] && [ "$owned" != "$uid" ]; then
            warn "$kind/$name was re-created since the install (uid differs) — not ours, kept"
            continue
        fi
        run "${K[@]}" delete "$kind" "$name" --wait=true >/dev/null
        if [ -z "$DRY_RUN" ]; then
            [ -z "$(uid_of "$kind")" ] || die "$kind/$name is still present after delete"
            log "    ok      $kind/$name deleted"
        fi
        [ "$kind" = serviceaccount ] && deleted_sa=1
    done

    if [ -n "$deleted_sa" ]; then
        # The chart's own binding went with the release; anything still naming the account was made
        # some other way — ykt3's hand-made team1/mlflow-trace-writers also grants otel-collector.
        # Only a dry run can still see the chart's own: the release is gone before a real run gets here.
        local found ours
        found="$({ kubectl --context "$KUBE_CONTEXT" get rolebindings,clusterrolebindings -A -o json 2>/dev/null \
                   || echo '{}'; } \
            | jq -r --arg sa "$SA" --arg ns "$NS" --arg rel "$RELEASE" '.items[]?
                | select(any(.subjects[]?; .kind == "ServiceAccount" and .name == $sa and .namespace == $ns))
                | (.metadata.annotations // {}) as $a
                | (if $a["meta.helm.sh/release-name"] == $rel and $a["meta.helm.sh/release-namespace"] == $ns
                   then "release" else "other" end)
                  + " \(.kind)/\(.metadata.namespace // "-")/\(.metadata.name)"')"
        ours="$(printf '%s\n' "$found" | sed -n 's/^release //p' | tr '\n' ' ')"
        bindings="$(printf '%s\n' "$found" | sed -n 's/^other //p' | tr '\n' ' ')"
        [ -z "$ours" ] || log "    release ${ours}— the chart's, removed with the Helm release"
        [ -z "$bindings" ] || warn "still naming the removed sa/$SA, and NOT touched (not ours): $bindings"
    fi
    run "${K[@]}" delete cm "$RECORD_CM" >/dev/null
    [ -n "$DRY_RUN" ] || log "    ok      cm/$RECORD_CM deleted"
}

TMP="$(umask 077; mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
REC=""
case "$CMD" in
    plan)      plan ;;
    install)   do_install ;;
    uninstall) do_uninstall ;;
esac
