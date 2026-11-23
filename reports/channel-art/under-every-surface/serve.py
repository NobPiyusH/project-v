import http.server, functools
class H(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*"); super().end_headers()
http.server.ThreadingHTTPServer(("127.0.0.1", 8799), functools.partial(H, directory=".")).serve_forever()
