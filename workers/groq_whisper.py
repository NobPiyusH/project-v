"""Whisper large-v3 on Groq (free tier): one multipart POST shared by the narration check (plain text) and the caption
word timings (verbose JSON). Callers decide what a failure means; this module only builds and sends the request."""

from __future__ import annotations

import json
import os
import urllib.request
import uuid

URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MODEL = "whisper-large-v3"
CRLF = "\r\n"


def multipart_body(wav: bytes, fields: list[tuple[str, str]], boundary: str) -> bytes:
    """multipart/form-data body: each (name, value) field in order, then the WAV as `file` (filename line.wav)."""
    head = "".join(f'--{boundary}{CRLF}Content-Disposition: form-data; name="{k}"{CRLF}{CRLF}{v}{CRLF}' for k, v in fields)
    head += (f'--{boundary}{CRLF}Content-Disposition: form-data; name="file"; filename="line.wav"{CRLF}'
             f"Content-Type: audio/wav{CRLF}{CRLF}")
    return head.encode() + wav + f"{CRLF}--{boundary}--{CRLF}".encode()


def transcribe(wav: bytes, extra_fields: list[tuple[str, str]] | None = None, timeout: float = 60) -> dict | None:
    """Groq's JSON response for `wav`, or None when GROQ_API_KEY is not set. Network/HTTP errors are raised."""
    key = os.getenv("GROQ_API_KEY")
    if not key:
        return None
    boundary = uuid.uuid4().hex
    body = multipart_body(wav, [("model", MODEL), *(extra_fields or [])], boundary)
    request = urllib.request.Request(URL, body, {
        "Authorization": "Bearer " + key, "Content-Type": f"multipart/form-data; boundary={boundary}",
        "User-Agent": "projectv"})
    with urllib.request.urlopen(request, timeout=timeout) as r:
        return json.load(r)
