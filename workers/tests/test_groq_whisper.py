import io
import json

from workers import groq_whisper as G


def test_body_matches_the_old_hand_built_narration_check_body():
    b, crlf = "B", "\r\n"
    old = (f'--{b}{crlf}Content-Disposition: form-data; name="model"{crlf}{crlf}whisper-large-v3{crlf}'
           f'--{b}{crlf}Content-Disposition: form-data; name="file"; filename="line.wav"{crlf}'
           f'Content-Type: audio/wav{crlf}{crlf}').encode() + b"WAV" + f"{crlf}--{b}--{crlf}".encode()
    assert G.multipart_body(b"WAV", [("model", G.MODEL)], b) == old


def test_no_key_means_no_call(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(G.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")))
    assert G.transcribe(b"WAV") is None


def test_sends_model_then_extra_fields_and_returns_json(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout):
        seen["body"], seen["auth"], seen["timeout"] = req.data, req.get_header("Authorization"), timeout
        return Resp(json.dumps({"text": "hi"}).encode())

    monkeypatch.setattr(G.urllib.request, "urlopen", urlopen)
    assert G.transcribe(b"WAV", [("language", "en")]) == {"text": "hi"}
    body = seen["body"].decode()
    assert body.index('name="model"') < body.index('name="language"') < body.index('name="file"')
    assert seen["auth"] == "Bearer k" and seen["timeout"] == 60
