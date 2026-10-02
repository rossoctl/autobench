# shellcheck shell=bash
# `--env-file FILE` for every install-side script. Source this; it defines functions and runs nothing.
#
#   . "$(dirname "${BASH_SOURCE[0]}")/envfile.sh"
#   envfile_prescan "$@" || exit 1      # FIRST, before any X="${X:-default}" line
#   ...normal flag loop, with:  --env-file) shift 2 ;;  --env-file=*) shift ;;
#
# PRECEDENCE, lowest to highest — "the last one wins":
#
#   1. the shell environment the script was started with
#   2. each --env-file, in command-line order; within a file, a later line beats an earlier one
#   3. explicit command-line flags
#
# The prescan is what makes (2) beat (1) and lose to (3): it assigns the file's values before the
# script reads its env defaults, and the flag loop runs after both. Values are exported, so a child
# script (autobench-install.sh -> ocp-service-bootstrap.sh) inherits them without re-reading the file.
#
# THE GRAMMAR, deliberately smaller than a shell's:
#
#   # a comment line            blank lines are skipped too
#   KEY=value                   the value is everything after the first `=`, surrounding blanks trimmed
#   export KEY=value            `export ` is accepted and ignored, so the file can also be sourced
#   KEY="value"  KEY='value'    ONE matching pair of quotes around the whole value is removed
#   KEY=                        sets KEY to the empty string — which every required check reads as unset
#
# Nothing is ever evaluated: no $VAR expansion, no $(...), no backslash escapes, no `eval`. A value is
# stored byte for byte, so a password containing `$` or a backtick is safe, and a file that somebody
# else wrote cannot run code. That is the difference from llm-profiles.sh's reader, which `eval`s and
# which only fills names that are still UNSET — the opposite precedence — and is left as it is.
#
# The file holds credentials, so it is refused unless it is chmod 600 or 400, and an error names the
# file, the line number and the key — never the value. reference/preflight.py implements the same
# grammar (`load_env_file`); tests/test_envfile.py holds the two to it.

# Load one file. Returns 1, having said why on stderr, on any malformed line or a readable-by-others file.
envfile_load() {  # $1=path
    local f="${1:-}" perm raw line key val n=0
    [ -n "$f" ] || { printf 'Error: --env-file: no path given\n' >&2; return 1; }
    [ -f "$f" ] || { printf 'Error: --env-file: no such file: %s\n' "$f" >&2; return 1; }
    perm="$(stat -f '%A' "$f" 2>/dev/null || stat -c '%a' "$f" 2>/dev/null || echo '')"
    case "$perm" in
        600|400) ;;
        *) printf 'Error: refusing --env-file %s: it holds credentials, so it must be chmod 600 (is %s)\n' \
               "$f" "${perm:-unknown}" >&2
           return 1 ;;
    esac
    while IFS= read -r raw || [ -n "$raw" ]; do
        n=$((n+1))
        line="${raw%$'\r'}"
        line="${line#"${line%%[![:space:]]*}"}"          # leading blanks
        case "$line" in ''|'#'*) continue ;; esac
        case "$line" in export[[:space:]]*) line="${line#export}"; line="${line#"${line%%[![:space:]]*}"}" ;; esac
        case "$line" in
            *=*) ;;
            *) printf 'Error: %s:%d: expected KEY=VALUE\n' "$f" "$n" >&2; return 1 ;;
        esac
        key="${line%%=*}"
        key="${key%"${key##*[![:space:]]}"}"             # trailing blanks before the `=`
        val="${line#*=}"
        val="${val#"${val%%[![:space:]]*}"}"
        val="${val%"${val##*[![:space:]]}"}"
        case "$key" in
            ''|[0-9]*|*[!A-Za-z0-9_]*)
                printf 'Error: %s:%d: invalid variable name %s\n' "$f" "$n" "'${key}'" >&2; return 1 ;;
        esac
        if [ "${#val}" -ge 2 ]; then
            case "$val" in
                \"*\") val="${val#\"}"; val="${val%\"}" ;;
                \'*\') val="${val#\'}"; val="${val%\'}" ;;
            esac
        fi
        printf -v "$key" '%s' "$val"
        export "${key?}"
    done < "$f"
    return 0
}

# Load every --env-file in "$@", in order. Only that flag is looked at; everything else is left for the
# caller's own loop, which must accept and skip `--env-file F` and `--env-file=F`.
envfile_prescan() {
    ENVFILE_LOADED=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --env-file)
                [ $# -ge 2 ] || { printf 'Error: --env-file needs a path\n' >&2; return 1; }
                envfile_load "$2" || return 1
                ENVFILE_LOADED="${ENVFILE_LOADED:+$ENVFILE_LOADED }$2"
                shift 2 ;;
            --env-file=*)
                envfile_load "${1#--env-file=}" || return 1
                ENVFILE_LOADED="${ENVFILE_LOADED:+$ENVFILE_LOADED }${1#--env-file=}"
                shift ;;
            *) shift ;;
        esac
    done
    return 0
}
