# shellcheck shell=bash
# Shared LLM-gateway profile resolution. Source this; it defines functions, runs nothing.
#
#   . "$(dirname "$0")/llm-profiles.sh"
#   LLM_PROFILE=intranet llm_profile_resolve
#
# WHY A PROFILE AND NOT A PLATFORM FLAG
#
# The gateway is any OpenAI-compatible LLM or LiteLLM service — no particular one is required, and
# every organisation's is its own. What a cluster may be pointed at depends on the NETWORK that
# service sits on, NOT on whether the cluster runs kind or OpenShift, and the rule is asymmetric:
# an Internet cluster of either platform must use a service on the Internet, because an intranet
# service is not routable from it, while an intranet cluster of either platform may use one on the
# intranet or on the Internet. Both platforms occur on both networks — OpenShift is commonly
# deployed on the intranet, and kind runs on an Internet server as readily as on a laptop on the
# VPN — so deriving the gateway from the platform is wrong, and it was wrong here until this file
# existed.
#
# No two services share a key table. A key from the wrong one does not fail at deploy — it
# 401s per completion, mid-run, and the leg finishes with zeroes that look like a measurement. That
# is the failure this indirection exists to prevent: one named profile carries the base, the model
# and the key file together, so they cannot be mixed by hand.
#
# WHAT A PROFILE IS
#
# Two independent sets of variables, distinguished only by prefix:
#
#   INTRANET_LLM_API_BASE   INTERNET_LLM_API_BASE     the gateway origin (scheme + host, no path)
#   INTRANET_LLM_MODEL      INTERNET_LLM_MODEL        a model id from THAT gateway's catalogue
#   INTRANET_LLM_KEY_FILE   INTERNET_LLM_KEY_FILE     chmod 600 file holding that gateway's key
#   INTRANET_LLM_NO_PROXY   INTERNET_LLM_NO_PROXY     optional explicit egress-proxy bypass list
#
# `LLM_PROFILE=intranet|internet` selects one, and llm_profile_resolve copies it into the canonical
# WORKLOAD_LLM_API_BASE / WORKLOAD_LLM_MODEL / LLM_KEY_FILE that every script already reads. An
# explicitly exported WORKLOAD_LLM_* always wins, so nothing that worked before this file behaves
# differently.
#
# The bases and model ids are not secrets, so keep them in a file instead of re-exporting per shell:
#
#   mkdir -p ~/.rossoctl-llm
#   cat > ~/.rossoctl-llm/profiles.env <<'EOF'
#   INTRANET_LLM_API_BASE=https://<intranet LLM service host>
#   INTRANET_LLM_MODEL=openai/aws/claude-haiku-4-5
#   INTERNET_LLM_API_BASE=https://<Internet LLM service host>
#   INTERNET_LLM_MODEL=openai/Azure/gpt-5-mini-2025-08-07
#   EOF
#
# The KEYS stay in their own per-profile files (default ~/.rossoctl-llm/<profile>.key, chmod 600) and
# are never part of profiles.env, never passed on argv, and never echoed.

LLM_PROFILE_ENV_FILE="${LLM_PROFILE_ENV_FILE:-$HOME/.rossoctl-llm/profiles.env}"
LLM_PROFILE_KEY_DIR="${LLM_PROFILE_KEY_DIR:-$HOME/.rossoctl-llm}"

# The profile named in a chart values file, so the values file and the instance file cannot disagree
# about which gateway this cluster talks to. Prints nothing when the file has no llmProfile key.
llm_profile_from_values() {  # $1=path to a values.yaml
    [ -f "${1:-}" ] || return 0
    sed -n 's/^llmProfile:[[:space:]]*"\{0,1\}\([A-Za-z]*\)"\{0,1\}[[:space:]]*$/\1/p' "$1" | head -1
}

# Resolve LLM_PROFILE into WORKLOAD_LLM_API_BASE / WORKLOAD_LLM_MODEL / LLM_KEY_FILE.
# No-op when LLM_PROFILE is empty: the caller's own defaults and flags stand.
llm_profile_resolve() {
    [ -n "${LLM_PROFILE:-}" ] || return 0

    local pfx
    case "$LLM_PROFILE" in
        intranet) pfx=INTRANET ;;
        internet) pfx=INTERNET ;;
        *) echo "LLM_PROFILE must be 'intranet' or 'internet', got '${LLM_PROFILE}'" >&2; return 1 ;;
    esac

    # profiles.env is read only for names that are still unset, so an export or a flag wins over it.
    if [ -f "$LLM_PROFILE_ENV_FILE" ]; then
        local line name val
        while IFS= read -r line || [ -n "$line" ]; do
            case "$line" in ''|\#*) continue ;; esac
            name="${line%%=*}"; val="${line#*=}"
            case "$name" in INTRANET_LLM_*|INTERNET_LLM_*) ;; *) continue ;; esac
            if [ -z "${!name:-}" ]; then
                val="${val%\"}"; val="${val#\"}"   # tolerate a quoted value
                eval "$name=\$val"
            fi
        done < "$LLM_PROFILE_ENV_FILE"
    fi

    local base_var="${pfx}_LLM_API_BASE" model_var="${pfx}_LLM_MODEL"
    local key_var="${pfx}_LLM_KEY_FILE" noproxy_var="${pfx}_LLM_NO_PROXY"
    local base="${!base_var:-}" model="${!model_var:-}"
    local key_file="${!key_var:-$LLM_PROFILE_KEY_DIR/${LLM_PROFILE}.key}"

    if [ -z "${WORKLOAD_LLM_API_BASE:-}" ]; then
        [ -n "$base" ] || { echo "LLM_PROFILE=${LLM_PROFILE} but ${base_var} is unset (export it, or put it in ${LLM_PROFILE_ENV_FILE})" >&2; return 1; }
        WORKLOAD_LLM_API_BASE="$base"
    elif [ -n "$base" ] && [ "$WORKLOAD_LLM_API_BASE" != "$base" ]; then
        # Not fatal — an explicit value is a deliberate override — but silence here is how a run ends
        # up measuring a gateway nobody selected.
        echo "NOTE: WORKLOAD_LLM_API_BASE overrides the ${LLM_PROFILE} profile's base" >&2
    fi
    [ -n "${WORKLOAD_LLM_MODEL:-}" ] || WORKLOAD_LLM_MODEL="$model"
    [ -n "${LLM_KEY_FILE:-}" ] || LLM_KEY_FILE="$key_file"
    [ -n "${WORKLOAD_LLM_NO_PROXY:-}" ] || WORKLOAD_LLM_NO_PROXY="${!noproxy_var:-}"

    export WORKLOAD_LLM_API_BASE WORKLOAD_LLM_MODEL LLM_KEY_FILE WORKLOAD_LLM_NO_PROXY
}

# True when BASE belongs to the selected profile, so a precheck can reject a config that points the
# workloads at one gateway while the key in openai-secret came from the other. Always true when no
# profile is selected (nothing to compare against) — absence of a declaration is not a mismatch.
llm_profile_base_matches() {  # $1=base to test
    [ -n "${LLM_PROFILE:-}" ] || return 0
    local pfx; case "$LLM_PROFILE" in intranet) pfx=INTRANET ;; internet) pfx=INTERNET ;; *) return 0 ;; esac
    local v="${pfx}_LLM_API_BASE"
    local expect="${!v:-}"
    [ -n "$expect" ] || return 0
    [ "${1:-}" = "$expect" ]
}

# The egress-proxy bypass list for the agent pod: loopback, the gateway host, and whatever
# in-cluster hosts the caller names. Where the cluster injects an HTTP_PROXY, an in-network gateway
# that is not on this list is unreachable and every completion leaves through a proxy that cannot
# see it. An explicit *_LLM_NO_PROXY wins, so a site with its own list keeps it.
llm_profile_no_proxy() {  # $@=extra hosts
    if [ -n "${WORKLOAD_LLM_NO_PROXY:-}" ]; then printf '%s' "$WORKLOAD_LLM_NO_PROXY"; return 0; fi
    local base="${WORKLOAD_LLM_API_BASE:-}" host list
    host="${base#*://}"; host="${host%%/*}"
    list="127.0.0.1,localhost"
    [ -n "$host" ] && list="${list},${host}"
    local h; for h in "$@"; do [ -n "$h" ] && list="${list},${h}"; done
    printf '%s' "$list"
}

# Read the selected profile's key into BM_WORKLOAD_LLM_KEY, refusing a file other users can read.
# Never echoes the value, and prints nothing on success.
llm_profile_load_key() {
    [ -z "${BM_WORKLOAD_LLM_KEY:-}" ] || return 0
    local f="${LLM_KEY_FILE:-}"
    [ -n "$f" ] && [ -f "$f" ] || return 0
    local perm
    perm="$(stat -f '%A' "$f" 2>/dev/null || stat -c '%a' "$f" 2>/dev/null || echo '')"
    case "$perm" in 600|400) ;; *) echo "refusing: $f must be chmod 600 (is ${perm:-unknown})" >&2; return 1 ;; esac
    IFS= read -r BM_WORKLOAD_LLM_KEY < "$f" || true
    export BM_WORKLOAD_LLM_KEY
}
