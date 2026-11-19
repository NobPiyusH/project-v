r"""One-time YouTube sign-in for the autopilot's Post button. The owner runs this himself, from the repo root:

    uv run python -m workers.youtube.auth C:\path\to\client_secret.json

It opens Google's consent page in the browser (upload permission only), catches the reply on 127.0.0.1, and saves
the refresh token to .secrets/youtube-token.json (git-ignored; also in the daily backup). Nothing secret is printed.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import sys
import urllib.parse
import webbrowser
from pathlib import Path

from workers.youtube import token

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def auth_url(client_id: str, redirect: str, state: str, challenge: str) -> str:
    return AUTH_URI + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": token.SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state, "code_challenge": challenge,
        "code_challenge_method": "S256"})


def read_client(path: str) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    client = data.get("installed") or data.get("web")
    if not client:
        raise SystemExit("That file is not a Google OAuth client (expected a Desktop app client_secret JSON).")
    return client


def main(argv: list[str]) -> None:
    if len(argv) != 1:
        raise SystemExit(__doc__)
    client = read_client(argv[0])
    verifier, challenge = pkce()
    state = secrets.token_urlsafe(16)
    reply: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            reply.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Done. You can close this tab and go back to the terminal.")

        def log_message(self, *args):  # keep the reply (it carries the code) out of the console
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}"
    url = auth_url(client["client_id"], redirect, state, challenge)
    print("Opening Google's sign-in page. If it does not open, paste this link into your browser:\n" + url)
    webbrowser.open(url)
    while "code" not in reply and "error" not in reply:
        server.handle_request()
    server.server_close()
    if reply.get("state") != state:
        raise SystemExit("The reply did not match this sign-in (state mismatch); nothing saved.")
    if "error" in reply:
        raise SystemExit("Google said: " + reply["error"] + "; nothing saved.")
    out = token.post_form(client.get("token_uri", token.TOKEN_URI), {
        "code": reply["code"], "client_id": client["client_id"], "client_secret": client["client_secret"],
        "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier})
    if "refresh_token" not in out:
        raise SystemExit("Google gave no refresh token; run this again (it asks for consent each time).")
    path = token.save({"client_id": client["client_id"], "client_secret": client["client_secret"],
                       "refresh_token": out["refresh_token"], "token_uri": client.get("token_uri", token.TOKEN_URI)})
    print(f"YouTube connected. Sign-in saved to {path} (keep it private).")


if __name__ == "__main__":
    main(sys.argv[1:])
