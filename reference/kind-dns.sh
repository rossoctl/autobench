#!/usr/bin/env bash
# KinD only: stop CoreDNS from turning one upstream DNS blip into a lost task, and undo that ONLY if
# this did it.
#
#   reference/kind-dns.sh plan      --context kind-rossoctl    # prints: harden | owned | ok | skip
#   reference/kind-dns.sh install   --context kind-rossoctl [--dry-run]
#   reference/kind-dns.sh uninstall --context kind-rossoctl [--dry-run]
#
# Called by autobench-install.sh and autobench-uninstall.sh; safe to run by hand.
#
# Why: a KinD node forwards DNS to its container runtime's resolver. On podman for macOS that is
# aardvark-dns and the gvproxy behind it, which now and then answers SERVFAIL — measured on kind
# 2026-10-04 at 25 of 2000 lookups in one window and 0 of 6000 in another, so it comes and goes.
# CoreDNS caches a SERVFAIL for 5 s (the `cache` plugin's default), so the resolver's own retry, and
# the agent's second probe 0.3 s later, get the same answer. The agent opens every task with a
# `GET /v1/models` probe, and a lookup that fails twice fails the task in under a second as
# `model_probe_failed` ("Temporary failure in name resolution") — which is how the 1-task smoke test
# was lost on 2026-10-04.
#
# The fix is one line in CoreDNS's `cache` block, `servfail 0`: never cache a SERVFAIL, so a retry
# goes upstream again. In a controlled A/B (same CoreDNS image, an upstream failing 5% of queries)
# it took lost lookups from 33 of 1500 to 0. The `reload` plugin picks the change up in place;
# nothing restarts.
#
# Deliberately NOT `serve_stale`: gvproxy answers with TTL 0, and against a TTL-0 upstream
# serve_stale refreshes on every request — 893,400 upstream queries against 1,100 in the same A/B.
#
# Ownership is a RECORD, never a guess: install writes cm/autobench-dns-install (kube-system) holding
# the Corefile as it was, and uninstall acts only when that record exists. A Corefile that was already
# hardened — by hand, or by a newer kind — has no record and is left alone. Uninstall puts the
# original back only if the Corefile is still exactly what install left; otherwise it saves the
# original to a file and changes nothing.
#
#   KIND_DNS        auto (default)  harden unless the cache block already has `servfail 0`
#                   never           touch nothing

set -euo pipefail
set +x

REFERENCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$REFERENCE_DIR")"
NS="kube-system"
RECORD_CM="autobench-dns-install"
COREDNS_CM="coredns"

log()  { printf '%s\n' "$*" >&2; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'Error: %s\n' "$*" >&2; exit 1; }

CMD="${1:-}"; [ $# -gt 0 ] && shift
KUBE_CONTEXT="${KUBE_CONTEXT:-}"
KIND_DNS="${KIND_DNS:-auto}"
DRY_RUN=""
RECORD_DIR="${RECORD_DIR:-}"
while [ $# -gt 0 ]; do
    case "$1" in
        --context)    KUBE_CONTEXT="${2:-}"; shift 2 ;;
        --mode)       KIND_DNS="${2:-}"; shift 2 ;;
        --record-dir) RECORD_DIR="${2:-}"; shift 2 ;;
        --dry-run)    DRY_RUN=1; shift ;;
        -h|--help)    sed -n '2,32p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done
case "$CMD" in plan|install|uninstall) ;; *) die "usage: kind-dns.sh plan|install|uninstall --context CTX [--dry-run]" ;; esac
[ -n "$KUBE_CONTEXT" ] || die "no context — pass --context or set KUBE_CONTEXT"
case "$KIND_DNS" in auto|never) ;; *) die "KIND_DNS must be auto or never (got '$KIND_DNS')" ;; esac
[ -d "$HOME/.rd/bin" ] && PATH="$PATH:$HOME/.rd/bin"
for t in kubectl jq shasum uv; do command -v "$t" >/dev/null 2>&1 || die "not on PATH: $t"; done

K=(kubectl --context "$KUBE_CONTEXT" -n "$NS")
run() { if [ -n "$DRY_RUN" ]; then printf '[dry-run] %s\n' "$*" >&2; else "$@"; fi; }

have_record() { "${K[@]}" get cm "$RECORD_CM" >/dev/null 2>&1; }
corefile() { "${K[@]}" get cm "$COREDNS_CM" -o json | jq -r '.data.Corefile // ""'; }
sha8() { shasum -a 256 | cut -c1-8; }
# sha8 of a ConfigMap's Corefile, byte for byte: jq -j keeps the trailing newline that $(...) strips.
corefile_sha() { jq -j '.data.Corefile // ""' | sha8; }
hardened() { grep -Eq '^[[:space:]]*servfail[[:space:]]+0[[:space:]]*$' <<<"$1"; }

# shellcheck source=reference/pyrun.sh
. "$REFERENCE_DIR/pyrun.sh"

plan() {
    if have_record; then echo owned; return; fi
    [ "$KIND_DNS" = never ] && { echo skip; return; }
    local cf; cf="$(corefile)" || die "no cm/$COREDNS_CM in $NS — is this a kind cluster?"
    if hardened "$cf"; then echo ok; else echo harden; fi
}

# The Corefile with `servfail 0` added to the cache block of the root (`.:53`) server. The edit
# is refused, not guessed, for a shape it does not know.
harden_corefile() {  # $1 = Corefile in, $2 = hardened Corefile out
    "${PY[@]}" - "$1" "$2" <<'PY'
import re, sys
cf = open(sys.argv[1]).read()
root = re.search(r"(?m)^\.:53 \{\n(?P<body>.*?)^\}", cf, re.S)
if not root:
    sys.exit("Error: no `.:53 {` server block in the Corefile — not the shape kind ships; edit it by hand")
body = root.group("body")
m = re.search(r"(?m)^(?P<ind>[ \t]+)cache(?P<args>[ \t]+[^{\n]*?)?[ \t]*(?P<open>\{)?[ \t]*$", body)
if not m:
    sys.exit("Error: the `.:53` block has no `cache` line — not the shape kind ships; edit it by hand")
ind = m.group("ind")
add = []
if not re.search(r"(?m)^[ \t]*servfail[ \t]+0[ \t]*$", body):
    add.append("servfail 0")
if m.group("open"):
    close = re.compile(r"(?m)^" + re.escape(ind) + r"\}[ \t]*$").search(body, m.end())
    if not close:
        sys.exit("Error: the `cache {` block has no closing brace at its own indent; edit it by hand")
    inner = re.search(r"(?m)^([ \t]+)\S", body[m.end():close.start()])
    sub = inner.group(1) if inner else ind + "   "
    new = body[:close.start()] + "".join(f"{sub}{a}\n" for a in add) + body[close.start():]
else:
    sub = ind + "   "
    line = f"{ind}cache{m.group('args') or ''} {{\n" + "".join(f"{sub}{a}\n" for a in add) + f"{ind}}}"
    new = body[:m.start()] + line + body[m.end():]
open(sys.argv[2], "w").write(cf[:root.start("body")] + new + cf[root.end("body"):])
PY
}

# The reload plugin re-reads the ConfigMap's mounted file — kubelet syncs it within ~1 min — and logs
# "Reloading complete" from every replica once the new config is live.
wait_reload() {  # $1 = RFC3339 time the patch was written
    local since="$1" pods want got
    pods="$("${K[@]}" get pods -l k8s-app=kube-dns -o name)"
    want="$(printf '%s\n' "$pods" | grep -c .)"
    for _ in $(seq 1 48); do
        got=0
        for p in $pods; do
            "${K[@]}" logs "$p" --since-time="$since" 2>/dev/null | grep -q 'Reloading complete' && got=$((got + 1))
        done
        if [ "$got" -eq "$want" ]; then log "    ok    all $want CoreDNS replicas reloaded the new Corefile"; return 0; fi
        sleep 5
    done
    die "only $got of $want CoreDNS replicas logged 'Reloading complete' in 240 s — check them: ${K[*]} logs -l k8s-app=kube-dns"
}

do_install() {
    local p; p="$(plan)"
    case "$p" in
        skip) log "    DNS: KIND_DNS=never — CoreDNS left as found"; return 0 ;;
        ok)   log "    DNS: CoreDNS's cache block already has servfail 0, and NOT by this script"
              log "         (no cm/$RECORD_CM) — left as is, and uninstall will leave it"; return 0 ;;
    esac
    repo_python "$REPO_DIR" || die "$REPO_PYTHON_HINT"
    local cm cf; cm="$("${K[@]}" get cm "$COREDNS_CM" -o json)"
    cf="$(jq -r '.data.Corefile' <<<"$cm")"
    if [ "$p" = owned ] && hardened "$cf"; then
        log "    DNS: owned by this script (cm/$RECORD_CM) and still hardened — nothing to do"
        return 0
    fi
    grep -Eq '^[[:space:]]*reload([[:space:]]|$)' <<<"$cf" \
        || die "the Corefile has no \`reload\` plugin, so a change would need a CoreDNS restart — not done automatically"
    jq -j '.data.Corefile' <<<"$cm" > "$TMP/Corefile"
    harden_corefile "$TMP/Corefile" "$TMP/Corefile.new" || exit 1
    if [ "$p" = harden ]; then
        log "    DNS: hardening CoreDNS's cache (servfail 0); recording the original in cm/$RECORD_CM"
        # The record goes in FIRST, so an install that dies half way is still undone by uninstall.
        jq --arg ns "$NS" --arg name "$RECORD_CM" --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
           --arg psha "$(corefile_sha <<<"$cm")" \
            '{apiVersion: "v1", kind: "ConfigMap",
              metadata: {name: $name, namespace: $ns,
                         labels: {"app.kubernetes.io/managed-by": "autobench-install.sh"}},
              data: {owner: "autobench-install.sh", installedAt: $at,
                     corefilePriorSha: $psha, corefilePrior: .data.Corefile}}' <<<"$cm" > "$TMP/record.json"
        run "${K[@]}" create -f "$TMP/record.json" >/dev/null
    else
        log "    DNS: owned by this script (cm/$RECORD_CM) but the hardening is gone — re-applying; the original stays as recorded"
    fi
    jq --rawfile cf "$TMP/Corefile.new" '.data.Corefile = $cf
        | del(.metadata.resourceVersion, .metadata.uid, .metadata.creationTimestamp, .metadata.managedFields)' \
        <<<"$cm" > "$TMP/coredns.json"
    if [ -n "$DRY_RUN" ]; then
        log "[dry-run] ${K[*]} replace -f <the Corefile, adding: $(diff "$TMP/Corefile" "$TMP/Corefile.new" | sed -n 's/^> *//p' | paste -sd, -)>"
        return 0
    fi
    local at; at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    "${K[@]}" replace -f "$TMP/coredns.json" >/dev/null
    "${K[@]}" patch cm "$RECORD_CM" --type merge \
        -p "{\"data\":{\"corefilePostSha\":\"$(sha8 < "$TMP/Corefile.new")\"}}" >/dev/null
    wait_reload "$at"
}

do_uninstall() {
    if ! have_record; then
        log "    kept    CoreDNS as found — not changed by autobench-install.sh (no cm/$RECORD_CM)"
        return 0
    fi
    local rec cm prior_sha post_sha cur_sha
    rec="$("${K[@]}" get cm "$RECORD_CM" -o json)"
    prior_sha="$(jq -r '.data.corefilePriorSha // ""' <<<"$rec")"
    post_sha="$(jq -r '.data.corefilePostSha // ""' <<<"$rec")"
    log "    record  cm/$RECORD_CM — installed $(jq -r '.data.installedAt' <<<"$rec") by $(jq -r '.data.owner' <<<"$rec")"
    cm="$("${K[@]}" get cm "$COREDNS_CM" -o json)"
    cur_sha="$(corefile_sha <<<"$cm")"
    if [ "$cur_sha" = "$prior_sha" ]; then
        log "    ok    the Corefile is already the recorded original ($cur_sha) — not touched"
    elif [ -n "$post_sha" ] && [ "$cur_sha" = "$post_sha" ]; then
        jq --argjson rec "$rec" '.data.Corefile = $rec.data.corefilePrior
            | del(.metadata.resourceVersion, .metadata.uid, .metadata.creationTimestamp, .metadata.managedFields)' \
            <<<"$cm" > "$TMP/coredns.json"
        local at; at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        run "${K[@]}" replace -f "$TMP/coredns.json" >/dev/null
        log "    ok    Corefile restored to the recorded original ($cur_sha -> $prior_sha)"
        [ -n "$DRY_RUN" ] || wait_reload "$at"
    else
        local keep="${RECORD_DIR:-$TMP}/coredns-Corefile.prior"
        jq -j '.data.corefilePrior' <<<"$rec" > "$keep"
        warn "the Corefile changed since the install ($cur_sha, neither the original $prior_sha nor what install left ${post_sha:-?}) — NOT restored; the original is in $keep"
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
