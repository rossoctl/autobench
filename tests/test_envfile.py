"""Covers the `--env-file` reader, which exists twice: `reference/envfile.sh` and `load_env_file` in
`reference/preflight.py`.

Neither ships in the package, and they are tested anyway because they are where install credentials
enter: a twin that drifts reads the SAME file differently depending on which script you ran, and the
precedence rule ("the last one wins") is a promise the template makes to the installer. Also held
here: the password intake's `<VAR>_FILE` spelling in `reference/credfile.sh`, which an env file uses
to keep the password itself out of the file.
"""

import os
import pathlib
import shutil
import subprocess
import sys

import pytest

REF = pathlib.Path(__file__).resolve().parent.parent / "reference"
sys.path.insert(0, str(REF))

import preflight  # noqa: E402

BASH = "/bin/bash" if os.path.exists("/bin/bash") else shutil.which("bash")

GRAMMAR = (
    "# a comment\n"
    "\n"
    "A=first\n"
    "B = spaced\n"
    "export C=exported\n"
    'D="double quoted"\n'
    "E='single quoted'\n"
    "F=$(touch PWNED)`touch PWNED2`$HOME\n"
    "G=a=b=c\n"
    "H=\n"
    'I="unbalanced\n'
    'J=  "  inner  "  \n'
    "\tK=tabbed\t\n"
    "L=crlf\r\n"
    "A=second\n"
    "M=no-trailing-newline"
)
EXPECTED = {
    "A": "second", "B": "spaced", "C": "exported", "D": "double quoted", "E": "single quoted",
    "F": "$(touch PWNED)`touch PWNED2`$HOME", "G": "a=b=c", "H": "", "I": '"unbalanced',
    "J": "  inner  ", "K": "tabbed", "L": "crlf", "M": "no-trailing-newline",
}


def _write(path: pathlib.Path, text: str, mode: int = 0o600) -> pathlib.Path:
    path.write_bytes(text.encode())
    path.chmod(mode)
    return path


def _bash_load(*files: pathlib.Path, env: dict | None = None, cwd=None) -> subprocess.CompletedProcess:
    """Run envfile_prescan over `--env-file F...` and dump the resulting environment, NUL-separated."""
    argv = []
    for f in files:
        argv += ["--env-file", str(f)]
    script = f'. "{REF}/envfile.sh"; envfile_prescan "$@" || exit 1; env -0'
    return subprocess.run([BASH, "-c", script, "bash", *argv], capture_output=True,
                          env=env if env is not None else {"PATH": os.environ["PATH"]}, cwd=cwd)


def _env_of(proc: subprocess.CompletedProcess) -> dict[str, str]:
    assert proc.returncode == 0, proc.stderr.decode()
    pairs = [p.split("=", 1) for p in proc.stdout.decode().split("\0") if "=" in p]
    return dict(pairs)


def test_both_twins_read_the_grammar_identically(tmp_path):
    f = _write(tmp_path / "a.env", GRAMMAR)
    shell = _env_of(_bash_load(f, cwd=tmp_path))
    python = preflight.load_env_file(str(f))
    assert python == EXPECTED
    assert {k: shell.get(k) for k in EXPECTED} == EXPECTED


def test_nothing_in_a_value_is_ever_run(tmp_path):
    f = _write(tmp_path / "a.env", GRAMMAR)
    _env_of(_bash_load(f, cwd=tmp_path))
    preflight.load_env_file(str(f))
    assert not (tmp_path / "PWNED").exists() and not (tmp_path / "PWNED2").exists()


def test_a_later_file_beats_an_earlier_one(tmp_path):
    a = _write(tmp_path / "a.env", "X=from-a\nY=only-a\n")
    b = _write(tmp_path / "b.env", "X=from-b\n")
    shell = _env_of(_bash_load(a, b))
    assert (shell["X"], shell["Y"]) == ("from-b", "only-a")
    merged: dict[str, str] = {}
    for f in (a, b):
        merged.update(preflight.load_env_file(str(f)))
    assert (merged["X"], merged["Y"]) == ("from-b", "only-a")


def test_a_file_beats_the_shell(tmp_path):
    f = _write(tmp_path / "a.env", "X=from-file\n")
    shell = _env_of(_bash_load(f, env={"PATH": os.environ["PATH"], "X": "from-shell", "Z": "kept"}))
    assert (shell["X"], shell["Z"]) == ("from-file", "kept")


@pytest.mark.parametrize("line, expect", [
    ("1BAD=hunter2-secret\n", "invalid variable name"),
    ("BAD-KEY=hunter2-secret\n", "invalid variable name"),
    ("hunter2-secret\n", "expected KEY=VALUE"),
])
def test_a_bad_line_names_file_and_line_but_never_the_value(tmp_path, line, expect):
    f = _write(tmp_path / "a.env", "OK=1\n" + line)
    proc = _bash_load(f)
    err = proc.stderr.decode()
    assert proc.returncode != 0 and f"{f}:2:" in err and expect in err and "hunter2" not in err
    with pytest.raises(ValueError) as exc:
        preflight.load_env_file(str(f))
    assert f"{f}:2:" in str(exc.value) and expect in str(exc.value)


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604])
def test_a_file_others_can_read_is_refused(tmp_path, mode):
    f = _write(tmp_path / "a.env", "X=1\n", mode)
    proc = _bash_load(f)
    assert proc.returncode != 0 and "chmod 600" in proc.stderr.decode()
    with pytest.raises(ValueError, match="chmod 600"):
        preflight.load_env_file(str(f))


def test_the_template_parses_and_blanks_nothing_in_the_shell(tmp_path):
    # A copied template that is only partly filled in must not clobber what the shell exported: an
    # uncommented `KEY=` is an assignment, and a file beats the shell.
    f = tmp_path / "copy.env"
    shutil.copy(REF / "autobench.env.template", f)
    f.chmod(0o600)
    assert preflight.load_env_file(str(f)) == {"KC_SERVICE_USERNAME": "benchmarker"}
    shell = _env_of(_bash_load(f, env={"PATH": os.environ["PATH"], "S3_ENABLED": "false"}))
    assert (shell["S3_ENABLED"], shell["KC_SERVICE_USERNAME"]) == ("false", "benchmarker")


def test_a_missing_file_is_an_error(tmp_path):
    assert _bash_load(tmp_path / "nope.env").returncode != 0
    with pytest.raises(ValueError, match="no such file"):
        preflight.load_env_file(str(tmp_path / "nope.env"))


# --- the installer: a flag beats a file --------------------------------------------------------------

INSTALL = REF / "autobench-install.sh"
_TOOLS = ("kubectl", "helm", "jq", "python3", "curl")
_PATH = os.environ["PATH"] + os.pathsep + str(pathlib.Path.home() / ".rd" / "bin")
needs_tools = pytest.mark.skipif(
    not all(shutil.which(t, path=_PATH) for t in _TOOLS),
    reason="autobench-install.sh checks for its tools before it reads any input")


def _install(*argv: str) -> subprocess.CompletedProcess:
    # No KUBE_CONTEXT anywhere, so a run that gets past the platform check stops at the context check
    # — before it touches a cluster.
    return subprocess.run([BASH, str(INSTALL), *argv], capture_output=True, text=True,
                          env={"PATH": _PATH, "HOME": str(pathlib.Path.home())})


@needs_tools
def test_install_reads_the_platform_from_the_file(tmp_path):
    f = _write(tmp_path / "a.env", "AB_PLATFORM=bogus\n")
    proc = _install("--env-file", str(f))
    assert proc.returncode != 0 and "AB_PLATFORM must be openshift or kind (got 'bogus')" in proc.stderr


@needs_tools
def test_install_flag_beats_the_file(tmp_path):
    f = _write(tmp_path / "a.env", "AB_PLATFORM=bogus\n")
    proc = _install("--env-file", str(f), "--platform", "kind")
    assert proc.returncode != 0 and "no context" in proc.stderr and "bogus" not in proc.stderr


# --- credfile.sh: <VAR> or <VAR>_FILE, never both -----------------------------------------------------


def _resolve(env: dict) -> subprocess.CompletedProcess:
    script = (f'. "{REF}/credfile.sh"; rc=0; cred_resolve_password KC_SERVICE_PASSWORD || rc=$?; '
              'printf "%s|%s|%s" "$rc" "${#KC_SERVICE_PASSWORD}" "$CRED_PASSWORD_SOURCE"')
    return subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                          env={"PATH": os.environ["PATH"], **env})


def test_password_from_the_variable():
    assert _resolve({"KC_SERVICE_PASSWORD": "abc"}).stdout == "0|3|env KC_SERVICE_PASSWORD"


def test_password_from_the_file_variable_drops_the_trailing_newline(tmp_path):
    f = _write(tmp_path / "pw", "abcd\n")
    out = _resolve({"KC_SERVICE_PASSWORD_FILE": str(f)}).stdout
    assert out == f"0|4|file {f} (KC_SERVICE_PASSWORD_FILE)"


def test_password_both_set_is_an_error(tmp_path):
    f = _write(tmp_path / "pw", "abcd\n")
    proc = _resolve({"KC_SERVICE_PASSWORD": "abc", "KC_SERVICE_PASSWORD_FILE": str(f)})
    assert proc.stdout.startswith("1|") and "both KC_SERVICE_PASSWORD and KC_SERVICE_PASSWORD_FILE" in proc.stderr


def test_password_none_set_is_rc_2():
    assert _resolve({}).stdout.startswith("2|0|")


def test_preflight_takes_the_same_file_variable(tmp_path, monkeypatch):
    f = _write(tmp_path / "pw", "abcd\n")
    args = preflight.argparse.Namespace(username=None, password_file=None, password_stdin=False,
                                        password=None)
    monkeypatch.delenv("KC_SERVICE_PASSWORD", raising=False)
    monkeypatch.setenv("KC_SERVICE_PASSWORD_FILE", str(f))
    _, pw, source, err = preflight.resolve_service_credential(args, None)
    assert (pw, err) == ("abcd", None) and "KC_SERVICE_PASSWORD_FILE" in source
    monkeypatch.setenv("KC_SERVICE_PASSWORD", "abc")
    _, pw, _, err = preflight.resolve_service_credential(args, None)
    assert pw is None and "both" in err
