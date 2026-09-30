import os, sys, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = os.environ["UPSTREAM_BASE"].rstrip("/")
KEY = os.environ.get("UPSTREAM_KEY", "")
TIMEOUT = float(os.environ.get("UPSTREAM_TIMEOUT", "60"))
HOP = {"host", "authorization", "content-length", "connection",
       "transfer-encoding", "keep-alive", "upgrade"}

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):  # keep the key out of any log line
        sys.stderr.write("%s %s -> %s\n" % (self.command, self.path.split("?")[0], a[1] if len(a) > 1 else ""))

    def _health(self):
        body = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _forward(self, method):
        if self.path in ("/healthz", "/health", "/ping"):
            return self._health()
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else None
        hdrs = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        if KEY:
            hdrs["Authorization"] = "Bearer " + KEY
        req = urllib.request.Request(UPSTREAM + self.path, data=body, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                data, status, ct = r.read(), r.status, r.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            data, status, ct = e.read(), e.code, e.headers.get("Content-Type", "application/json")
        except Exception as e:
            data, status, ct = ('{"error":"upstream: %s"}' % type(e).__name__).encode(), 502, "application/json"
        self.send_response(status)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._forward("GET")

    def do_POST(self):
        self._forward("POST")

ThreadingHTTPServer(("0.0.0.0", 8080), H).serve_forever()
