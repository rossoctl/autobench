"""Covers `reference/keycloak-ensure-user.sh` against a fake Keycloak Admin API, and the preflight hint
that points at it.

The script is what preflight tells an installer to run when the `benchmarker` check fails, so it has
to leave a user that actually logs in: on the rossoctl realm that means firstName/lastName set and no
pending required action, not just a password — and the `rossoctl-operator` role for /deploy.
"""

import json
import pathlib
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "reference"))

import preflight  # noqa: E402

SCRIPT = ROOT / "reference" / "keycloak-ensure-user.sh"
PASSWORD = "s3cret-for-tests"


class FakeKeycloak:
    """One realm (`rossoctl`), one client, an optional role; enough state for the script's calls."""

    def __init__(self, user: dict | None = None, role: bool = True):
        self.users = {user["id"]: user} if user else {}
        self.passwords: dict[str, str] = {}
        self.roles: dict[str, list[str]] = {}
        self.role = {"id": "r1", "name": "rossoctl-operator"} if role else None
        self.client = {"id": "c1", "clientId": "rossoctl", "directAccessGrantsEnabled": False}

    def by_name(self, name):
        return [u for u in self.users.values() if u["username"] == name]


def _handler(kc: FakeKeycloak):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep pytest output clean
            pass

        def _send(self, code, body=None):
            data = b"" if body is None else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
            if self.headers.get("Content-Type", "").startswith("application/json"):
                return json.loads(raw or "null")
            return dict(urllib.parse.parse_qsl(raw))

        def do_POST(self):
            url = urllib.parse.urlparse(self.path)
            body = self._body()
            if url.path == "/realms/master/protocol/openid-connect/token":
                return self._send(200, {"access_token": "admin-token"})
            if url.path == "/realms/rossoctl/protocol/openid-connect/token":
                [u] = kc.by_name(body["username"]) or [None]
                if not u or kc.passwords.get(u["id"]) != body["password"]:
                    return self._send(401, {"error": "invalid_grant"})
                if not u.get("firstName") or not u.get("lastName") or u.get("requiredActions"):
                    return self._send(400, {"error": "invalid_grant",
                                            "error_description": "Account is not fully set up"})
                return self._send(200, {"access_token": "user-token"})
            if url.path == "/admin/realms/rossoctl/users":
                uid = f"u{len(kc.users) + 1}"
                kc.users[uid] = {"id": uid, **body}
                return self._send(201)
            if url.path.endswith("/role-mappings/realm"):
                uid = url.path.split("/")[5]
                kc.roles.setdefault(uid, [])
                for r in body:
                    if r["name"] not in kc.roles[uid]:
                        kc.roles[uid].append(r["name"])
                return self._send(204)
            self._send(404, {"error": "unknown"})

        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            q = dict(urllib.parse.parse_qsl(url.query))
            p = url.path
            if p == "/admin/realms/rossoctl/users":
                return self._send(200, kc.by_name(q["username"]))
            if p.startswith("/admin/realms/rossoctl/users/"):
                return self._send(200, kc.users[p.rsplit("/", 1)[1]])
            if p == "/admin/realms/rossoctl/roles/rossoctl-operator":
                return self._send(200, kc.role) if kc.role else self._send(404, {"error": "nf"})
            if p == "/admin/realms/rossoctl/clients":
                return self._send(200, [kc.client] if q.get("clientId") == "rossoctl" else [])
            self._send(404, {"error": "unknown"})

        def do_PUT(self):
            p = urllib.parse.urlparse(self.path).path
            body = self._body()
            if p.endswith("/reset-password"):
                kc.passwords[p.split("/")[5]] = body["value"]
                return self._send(204)
            if p.startswith("/admin/realms/rossoctl/users/"):
                kc.users[p.rsplit("/", 1)[1]] = body
                return self._send(204)
            if p == "/admin/realms/rossoctl/clients/c1":
                kc.client = body
                return self._send(204)
            self._send(404, {"error": "unknown"})

    return H


@pytest.fixture
def keycloak():
    servers = []

    def start(**kw):
        kc = FakeKeycloak(**kw)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _handler(kc))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return kc, f"http://127.0.0.1:{srv.server_address[1]}"

    yield start
    for s in servers:
        s.shutdown()


def run(server, *extra, env_extra=None):
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "KC_ADMIN_PASSWORD": "admin",
           **(env_extra if env_extra is not None else {"KC_USER_PASSWORD": PASSWORD})}
    return subprocess.run(
        ["bash", str(SCRIPT), "--server", server, "--realm", "rossoctl", "--client", "rossoctl",
         "--username", "benchmarker", *extra],
        env=env, capture_output=True, text=True, timeout=60,
    )


def test_fresh_user_ends_up_able_to_log_in_with_the_role(keycloak):
    kc, server = keycloak()
    r = run(server, "--realm-role", "rossoctl-operator", "--verify")
    assert r.returncode == 0, r.stderr
    [u] = kc.by_name("benchmarker")
    assert (u["firstName"], u["lastName"], u["requiredActions"], u["enabled"]) == (
        "Bench", "Marker", [], True)
    assert kc.roles[u["id"]] == ["rossoctl-operator"]
    assert kc.client["directAccessGrantsEnabled"] is True
    assert "token obtained" in r.stderr
    assert PASSWORD not in r.stdout + r.stderr


def test_existing_names_are_kept_and_required_actions_cleared(keycloak):
    kc, server = keycloak(user={"id": "u9", "username": "benchmarker", "enabled": False,
                                "firstName": "Ada", "lastName": "", "requiredActions": ["UPDATE_PASSWORD"]})
    r = run(server, "--verify")
    assert r.returncode == 0, r.stderr
    u = kc.users["u9"]
    assert (u["firstName"], u["lastName"], u["requiredActions"], u["enabled"]) == (
        "Ada", "Marker", [], True)
    assert "u9" not in kc.roles  # no --realm-role, no grant


def test_rerun_is_idempotent(keycloak):
    kc, server = keycloak()
    for _ in range(2):
        assert run(server, "--realm-role", "rossoctl-operator").returncode == 0
    [u] = kc.by_name("benchmarker")
    assert len(kc.users) == 1 and kc.roles[u["id"]] == ["rossoctl-operator"]


def test_missing_role_fails_loudly(keycloak):
    _, server = keycloak(role=False)
    r = run(server, "--realm-role", "rossoctl-operator")
    assert r.returncode != 0
    assert "realm role 'rossoctl-operator' not found in realm 'rossoctl'" in r.stderr


def test_password_from_file(keycloak, tmp_path):
    kc, server = keycloak()
    f = tmp_path / "pass"
    f.write_text(PASSWORD + "\n")
    f.chmod(0o600)
    r = run(server, "--verify", env_extra={"KC_USER_PASSWORD_FILE": str(f)})
    assert r.returncode == 0, r.stderr
    [u] = kc.by_name("benchmarker")
    assert kc.passwords[u["id"]] == PASSWORD  # trailing newline stripped


def test_no_password_names_both_ways(keycloak):
    _, server = keycloak()
    r = run(server, env_extra={})
    assert r.returncode != 0
    assert "KC_USER_PASSWORD (or KC_USER_PASSWORD_FILE)" in r.stderr


def test_preflight_howto_is_filled_in_and_carries_no_secret():
    text = preflight.ensure_user_howto("https://kc.example.com", "rossoctl", "rossoctl", "benchmarker")
    assert "reference/keycloak-ensure-user.sh --server https://kc.example.com --realm rossoctl" in text
    assert "--username benchmarker --realm-role rossoctl-operator --verify" in text
    assert "KC_ADMIN_PASSWORD=<" in text and "KC_USER_PASSWORD_FILE=<" in text


def test_hint_is_printed_but_not_counted(capsys):
    rep = preflight.Report()
    rep.fail("ROPC login as benchmarker", "x")
    rep.hint("line one\nline two")
    assert rep.count(preflight.FAIL) == 1 and len(rep.rows) == 1
    out = capsys.readouterr().out
    assert "        line one\n        line two" in out
