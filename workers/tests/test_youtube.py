"""YouTube posting: metadata limits, the resumable upload, token refresh and the sign-in link (no network)."""

import io
import json
import urllib.parse

import pytest

from workers.youtube import auth, token, upload


class FakeResponse(io.BytesIO):
    def __init__(self, body=b"", headers=None):
        super().__init__(body)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_metadata_marks_ai_content_and_keeps_limits():
    m = upload.metadata({"title": "x" * 150, "description": "d", "tags": ["tag" * 20] * 20, "privacy": "unlisted"})
    assert len(m["snippet"]["title"]) == 100
    assert sum(len(t) + 1 for t in m["snippet"]["tags"]) <= 450
    assert m["status"] == {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True}


def test_upload_opens_a_session_then_puts_the_file(tmp_path):
    f = tmp_path / "s.mp4"
    f.write_bytes(b"video-bytes")
    calls = []

    def opener(req, timeout):
        calls.append(req)
        if req.get_method() == "POST":
            return FakeResponse(headers={"Location": "https://upload.example/session"})
        return FakeResponse(json.dumps({"id": "abc123", "status": {"privacyStatus": "private"}}).encode())

    out = upload.upload_file(str(f), {"title": "T", "tags": ["dream"], "privacy": "public"}, "tok", opener)
    assert out["id"] == "abc123"
    assert [c.get_method() for c in calls] == ["POST", "PUT"]
    assert json.loads(calls[0].data)["snippet"]["title"] == "T"
    assert calls[1].full_url == "https://upload.example/session" and calls[1].data == b"video-bytes"
    assert calls[1].get_header("Authorization") == "Bearer tok"


def test_upload_activity_reports_failure_instead_of_raising(monkeypatch):
    monkeypatch.setattr(token, "access_token", lambda: (_ for _ in ()).throw(RuntimeError("YouTube is not connected yet")))
    out = upload.youtube_upload({"runId": "r", "videoUri": "s3://b/k.mp4", "title": "T", "privacy": "public"})
    assert out["videoId"] is None and "not connected" in out["error"] and out["privacy"] == "public"


def test_access_token_uses_the_saved_refresh_token(tmp_path, monkeypatch):
    monkeypatch.setenv("YOUTUBE_TOKEN_FILE", str(tmp_path / "t.json"))
    token.save({"client_id": "cid", "client_secret": "sec", "refresh_token": "ref"})
    seen = {}

    def opener(req, timeout):
        seen.update(urllib.parse.parse_qs(req.data.decode()))
        return FakeResponse(json.dumps({"access_token": "fresh"}).encode())

    assert token.access_token(opener) == "fresh"
    assert seen["grant_type"] == ["refresh_token"] and seen["refresh_token"] == ["ref"]


def test_not_connected_says_how_to_connect(tmp_path, monkeypatch):
    monkeypatch.setenv("YOUTUBE_TOKEN_FILE", str(tmp_path / "missing.json"))
    with pytest.raises(RuntimeError, match="workers.youtube.auth"):
        token.load()


def test_sign_in_link_asks_only_for_upload_with_pkce():
    verifier, challenge = auth.pkce()
    q = urllib.parse.parse_qs(urllib.parse.urlparse(auth.auth_url("cid", "http://127.0.0.1:5000", "st", challenge)).query)
    assert q["scope"] == [token.SCOPE] and q["code_challenge_method"] == ["S256"] and q["access_type"] == ["offline"]
    assert q["state"] == ["st"] and verifier != challenge
