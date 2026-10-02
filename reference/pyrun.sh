# shellcheck shell=bash
# The one Python the install and uninstall scripts run: the repo's own uv environment, pinned by
# uv.lock. Source this; it defines a function and runs nothing.
#
#   . "$REFERENCE_DIR/pyrun.sh"
#   repo_python "$REPO_DIR" || die "$REPO_PYTHON_HINT"
#   "${PY[@]}" "$REFERENCE_DIR/preflight.py" ...
#
# Never the python3 on PATH. That is whatever the workstation happens to have — macOS's is 3.9 with no
# PyYAML, below the package's own >=3.11 — and each difference between it and ours became a script
# that failed, or quietly guessed, on the operator's machine only. uv builds the environment from
# uv.lock on first use, so there is nothing to set up, and --frozen never rewrites the lock file.

# shellcheck disable=SC2034  # read by the caller that sources this
REPO_PYTHON_HINT="these scripts run Python through uv, which is not on PATH — install it: https://docs.astral.sh/uv/getting-started/installation/"

# shellcheck disable=SC2034  # PY is the result, read by the caller
repo_python() {
    local repo="$1"
    command -v uv >/dev/null 2>&1 || return 1
    PY=(uv run --project "$repo" --frozen --quiet python)
    # Built (or synced) here, once and visibly, rather than half way into the first real call.
    if ! "${PY[@]}" -c 'import yaml' >&2; then
        REPO_PYTHON_HINT="uv could not build the repo's environment (above) — try: uv sync --project $repo"
        return 1
    fi
}
