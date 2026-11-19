"""The owner's YouTube sign-in, kept in a git-ignored file (never in logs).

The file holds the OAuth client id/secret and the refresh token that ``python -m workers.youtube.auth`` saved. Only
the upload scope is granted: the studio can upload videos to the owner's channel, nothing else.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"


def token_path() -> Path:
    return Path(os.getenv("YOUTUBE_TOKEN_FILE", ".secrets/youtube-token.json"))


def load() -> dict:
    p = token_path()
    if not p.exists():
        raise RuntimeError("YouTube is not connected yet: run `uv run python -m workers.youtube.auth <client_secret.json>` once")
    return json.loads(p.read_text(encoding="utf-8"))


def save(data: dict) -> Path:
    p = token_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def post_form(url: str, fields: dict, opener=urllib.request.urlopen) -> dict:
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with opener(req, timeout=30) as r:
        return json.loads(r.read().decode())


def access_token(opener=urllib.request.urlopen) -> str:
    """A fresh access token from the saved refresh token (valid about an hour)."""
    t = load()
    out = post_form(t.get("token_uri", TOKEN_URI), {"client_id": t["client_id"], "client_secret": t["client_secret"],
                                                    "refresh_token": t["refresh_token"], "grant_type": "refresh_token"}, opener)
    if "access_token" not in out:
        raise RuntimeError("YouTube sign-in was refused (" + str(out.get("error", "unknown")) + "); run the sign-in again")
    return out["access_token"]
