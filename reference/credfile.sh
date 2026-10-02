# shellcheck shell=bash
# Credential intake, shared by the scripts that need the `benchmarker` password. Source this; it
# defines functions and runs nothing.
#
#   . "$(dirname "$0")/credfile.sh"
#   CRED_PASSWORD_FILE=~/.rossoctl-kind/benchmarker.pass
#   cred_resolve_password KC_SERVICE_PASSWORD || die "no password"
#
# WHY FOUR WAYS IN AND ONE OF THEM DISCOURAGED
#
# Every one of these scripts needs a password it must not leak, and the four intake paths are not
# equivalent:
#
#   --password-file   a chmod-600 file. Preferred: the value never appears in argv or in an
#                     environment, it survives a cluster rebuild, and the permission check below
#                     refuses a file somebody else on the machine can read.
#   --password-stdin  for a pipeline or a secret manager: `pass show … | script --password-stdin`.
#   --password        accepted because it is the obvious thing to reach for, but argv is
#                     world-readable (`ps`, /proc/<pid>/cmdline) and lands in shell history, so it
#                     warns every time.
#   the environment   the original path: the value in <VAR>, or a chmod-600 file named by <VAR>_FILE
#                     (e.g. KC_SERVICE_PASSWORD_FILE) — the form an --env-file should use.
#
# Precedence is that order, most explicit first. The resolved value is exported under the name the
# caller asks for, because these scripts hand values to `jq` through the environment rather than
# through `--arg` for exactly the argv reason above.

# Read a credential from a file. Prints the value on stdout for `$(...)` capture and nothing else,
# so it is safe to use in a script that must never echo a secret to a terminal.
cred_read_file() {  # $1=path
    local f="${1:-}" perm line
    [ -n "$f" ] || { printf 'Error: cred_read_file: no path given\n' >&2; return 1; }
    [ -f "$f" ] || { printf 'Error: no such credential file: %s\n' "$f" >&2; return 1; }
    perm="$(stat -f '%A' "$f" 2>/dev/null || stat -c '%a' "$f" 2>/dev/null || echo '')"
    case "$perm" in
        600|400) ;;
        *) printf 'Error: refusing: %s must be chmod 600 (is %s)\n' "$f" "${perm:-unknown}" >&2
           return 1 ;;
    esac
    # `read` stops at the first newline, which is the point: an editor's trailing newline welded
    # onto a password fails ROPC with exactly the message a WRONG password gives.
    IFS= read -r line < "$f" || true
    printf '%s' "$line"
}

# Resolve a password into the variable named by $1, and record where it came from in
# CRED_PASSWORD_SOURCE (safe to print — it is a source, not a value).
# Returns 1 on a bad file, 2 when no source supplied anything.
cred_resolve_password() {  # $1=variable name, e.g. KC_SERVICE_PASSWORD
    local var="${1:?cred_resolve_password: variable name required}" val=""
    CRED_PASSWORD_SOURCE=""
    if [ -n "${CRED_PASSWORD_FILE:-}" ]; then
        val="$(cred_read_file "$CRED_PASSWORD_FILE")" || return 1
        CRED_PASSWORD_SOURCE="file ${CRED_PASSWORD_FILE}"
    elif [ -n "${CRED_PASSWORD_STDIN:-}" ]; then
        IFS= read -r val || true
        CRED_PASSWORD_SOURCE="stdin"
    elif [ -n "${CRED_PASSWORD_ARGV:-}" ]; then
        val="$CRED_PASSWORD_ARGV"
        CRED_PASSWORD_SOURCE="--password (argv)"
        printf 'WARNING: the password was passed on the command line; argv is world-readable (ps,\n' >&2
        printf '         /proc/<pid>/cmdline) and it lands in your shell history — prefer\n' >&2
        printf '         --password-file <chmod-600 file> or --password-stdin\n' >&2
    else
        # The environment, in either of two spellings: the value itself, or <VAR>_FILE naming a
        # chmod-600 file that holds it — the form an --env-file wants, since it keeps the password
        # out of that file. Both at once is ambiguous, so it is an error rather than a guess.
        local fvar="${var}_FILE"
        if [ -n "${!fvar:-}" ] && [ -n "${!var:-}" ]; then
            printf 'Error: both %s and %s are set — set one (an --env-file line `%s=` clears the other)\n' \
                "$var" "$fvar" "$var" >&2
            return 1
        elif [ -n "${!fvar:-}" ]; then
            val="$(cred_read_file "${!fvar}")" || return 1
            CRED_PASSWORD_SOURCE="file ${!fvar} (${fvar})"
        else
            val="${!var:-}"
            [ -n "$val" ] && CRED_PASSWORD_SOURCE="env ${var}"
        fi
    fi
    [ -n "$val" ] || return 2
    eval "$var=\$val"
    export "${var?}"
    return 0
}

# The realm roles inside an access token, comma-joined. Prints nothing if it cannot decode one.
#
# The padding matters: JWT segments are base64url with the `=` stripped, and `base64 -d` fed an
# unpadded segment decodes a PREFIX and exits non-zero. Piped into jq that is truncated JSON, so the
# role list comes back empty and the caller reports a missing role on a token that carries it — which
# is exactly what both bootstrap scripts did before this function existed.
cred_jwt_realm_roles() {  # $1=access token
    local p="${1:-}"
    p="${p#*.}"; p="${p%%.*}"
    [ -n "$p" ] || return 0
    case $(( ${#p} % 4 )) in 2) p="${p}==" ;; 3) p="${p}=" ;; esac
    printf '%s' "$p" | tr '_-' '/+' | base64 -d 2>/dev/null \
        | jq -r '.realm_access.roles // [] | join(",")' 2>/dev/null || true
}

# Classify a Keycloak ROPC refusal. $1=http status, $2=response body.
#
# Worth the twenty lines because the three failures need three different fixes and Keycloak reports
# two of them identically: `invalid_grant` is the answer for a wrong password, for a user with no
# password credential, AND for no such user. Only an admin credential separates those, which is
# what reference/preflight.py does with one; here we name the possibilities instead of guessing.
cred_ropc_cause() {  # $1=status $2=body -> prints a one-line cause
    local status="${1:-0}" body="${2:-}" err desc
    err="$(printf '%s' "$body" | jq -r '.error // empty' 2>/dev/null || true)"
    desc="$(printf '%s' "$body" | jq -r '.error_description // empty' 2>/dev/null || true)"
    case "$(printf '%s %s' "$err" "$desc" | tr 'A-Z' 'a-z')" in
        *"not fully set up"*)
            printf '%s' "'Account is not fully set up' — the PASSWORD may be correct; the user has a pending required action, or no firstName/lastName, which the rossoctl realm's user profile requires before it will issue a token" ;;
        *disabled*)
            printf '%s' "the account is disabled in Keycloak" ;;
        *"invalid user credentials"*|invalid_grant*)
            printf '%s' "'Invalid user credentials' — the password is wrong, the user has no password credential, or the user does not exist; Keycloak answers all three identically. \`python3 reference/preflight.py --password-file …\` distinguishes them when a Keycloak admin credential is available" ;;
        unauthorized_client*|invalid_client*)
            printf '%s' "${err} — the client is confidential or has directAccessGrantsEnabled=false; set KC_SERVICE_CLIENT_SECRET, or run reference/keycloak-ensure-user.sh which enables Direct Access Grants idempotently" ;;
        *)
            printf 'HTTP %s: %s' "$status" "${desc:-${err:-no response}}" ;;
    esac
}
