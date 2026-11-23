"""Tiny token-protected server that runs ON the pod (stdlib only), port 8189.
  POST /x/<tok>              body = bash command, runs it (110 s cap), returns "[exit N]\\n<output>"
  PUT  /up/<tok>/<relpath>?off=N   body = bytes, written at offset N under /workspace/ms/<relpath>
  GET  /get/<tok>/<relpath>  file under /workspace/ms/
  GET  /ready                200 if /workspace/READY exists (Blender installed)
"""
import http.server, os, subprocess, urllib.parse

TOKEN = os.environ["EXEC_TOKEN"]
ROOT = "/workspace/ms"
os.makedirs(ROOT, exist_ok=True)


class H(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body=b"", ctype="text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _path(self, prefix):
        u = urllib.parse.urlparse(self.path)
        if not u.path.startswith(prefix + TOKEN + "/"):
            return None, None
        rel = os.path.normpath(urllib.parse.unquote(u.path[len(prefix) + len(TOKEN) + 1:])).lstrip("/\\")
        if rel.startswith(".."):
            return None, None
        return os.path.join(ROOT, rel), urllib.parse.parse_qs(u.query)

    def do_POST(self):
        if self.path != "/x/" + TOKEN:
            return self._send(403)
        cmd = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        try:
            r = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, timeout=110)
            out, code = (r.stdout + r.stderr)[-60000:], r.returncode
        except Exception as e:  # noqa: BLE001
            out, code = str(e), -1
        self._send(200, ("[exit %s]\n%s" % (code, out)).encode())

    def do_PUT(self):
        p, q = self._path("/up/")
        if not p:
            return self._send(403)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        off = int((q.get("off") or ["0"])[0])
        n = int(self.headers.get("Content-Length", 0))
        with open(p, "r+b" if os.path.exists(p) else "wb") as f:
            f.seek(off)
            f.write(self.rfile.read(n))
        self._send(200, str(os.path.getsize(p)).encode())

    def do_GET(self):
        if self.path.startswith("/ready"):
            return self._send(200 if os.path.exists("/workspace/READY") else 503)
        if self.path == "/log/" + TOKEN:
            try:
                return self._send(200, open("/workspace/logs/boot.log", "rb").read()[-6000:])
            except Exception as e:  # noqa: BLE001
                return self._send(200, str(e).encode())
        p, _ = self._path("/get/")
        if not p or not os.path.isfile(p):
            return self._send(404)
        b = open(p, "rb").read()
        self._send(200, b, "application/octet-stream")

    def log_message(self, *a):
        pass


http.server.ThreadingHTTPServer(("0.0.0.0", 8189), H).serve_forever()
