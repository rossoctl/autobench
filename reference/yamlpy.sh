# shellcheck shell=bash
# A python that can import yaml, for the reference scripts that read the collector's config — one
# embedded YAML document, which kind-collector-mlflow.py parses. Source this; it defines a function
# and runs nothing.
#
#   . "$REFERENCE_DIR/yamlpy.sh"
#   yaml_python "$REPO_DIR" || die "$YAML_PYTHON_HINT"
#   "${PY[@]}" "$REFERENCE_DIR/kind-collector-mlflow.py" ...
#
# python3 first, then the repo's uv environment, which carries PyYAML as a dependency. A workstation
# python3 often has none (macOS's does not), and a caller that swallowed that failure used to fall
# back to a guessed value without a word.

# shellcheck disable=SC2034  # read by the caller that sources this
YAML_PYTHON_HINT="reading the collector's config needs PyYAML: python3 has none and uv is not available — pip install pyyaml, or install uv"

# shellcheck disable=SC2034  # PY is the result, read by the caller
yaml_python() {
    local repo="$1"
    if python3 -c 'import yaml' >/dev/null 2>&1; then
        PY=(python3)
    elif command -v uv >/dev/null 2>&1 \
        && uv run --project "$repo" --quiet python -c 'import yaml' >/dev/null 2>&1; then
        PY=(uv run --project "$repo" --quiet python)
    else
        return 1
    fi
}
