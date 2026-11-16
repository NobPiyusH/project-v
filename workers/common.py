"""Shared helpers for media activities: settings, S3 storage (SeaweedFS), Gemini client, audio utilities.

All requests and results crossing the Java <-> Python boundary are plain dicts with camelCase keys, mirroring the
Java records in com.projectv.media.MediaTypes. Media bytes never go through Temporal: only s3:// URIs.
"""

from __future__ import annotations

import functools
import io
import logging
import os
import random
import re
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path

import boto3
from botocore.config import Config
from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

log = logging.getLogger("projectv.media")

load_dotenv()

BUCKET = os.getenv("S3_BUCKET", "projectv")


# ----------------------------------------------------------------------------------------------------------- storage

@functools.cache
def s3():
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("S3_ENDPOINT", "http://localhost:8333"),
        aws_access_key_id=os.getenv("S3_ACCESS_KEY", "projectv"),
        aws_secret_access_key=os.getenv("S3_SECRET_KEY", "projectv-local-secret"),
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 5}),
    )


def ensure_bucket() -> None:
    try:
        s3().head_bucket(Bucket=BUCKET)
    except Exception:
        s3().create_bucket(Bucket=BUCKET)


def put_bytes(key: str, data: bytes, content_type: str) -> str:
    s3().put_object(Bucket=BUCKET, Key=key, Body=data, ContentType=content_type)
    return f"s3://{BUCKET}/{key}"


def put_file(key: str, path: Path, content_type: str) -> str:
    s3().upload_file(str(path), BUCKET, key, ExtraArgs={"ContentType": content_type})
    return f"s3://{BUCKET}/{key}"


def _split(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://"):
        raise ValueError(f"Not an s3 uri: {uri}")
    bucket, _, key = uri[5:].partition("/")
    return bucket, key


def download(uri: str, dest: Path) -> Path:
    bucket, key = _split(uri)
    s3().download_file(bucket, key, str(dest))
    return dest


def get_bytes(uri: str) -> bytes:
    bucket, key = _split(uri)
    return s3().get_object(Bucket=bucket, Key=key)["Body"].read()


# ----------------------------------------------------------------------------------------------------------- gemini

_client: genai.Client | None = None
_client_lock = threading.Lock()


def gemini() -> genai.Client:
    """One shared client for the process. A throwaway client (``gemini().models...``) can be garbage-collected, and
    so closed, while its request is still running ("client has been closed"), so the client is kept alive here."""
    global _client
    with _client_lock:
        if _client is None:
            key = os.getenv("GEMINI_API_KEY")
            if not key:
                raise RuntimeError("GEMINI_API_KEY is not set in .env")
            # v1beta: Lyria, Nano Banana and the TTS models are only served there (the SDK defaults to v1).
            # Generous timeout: media models (music, images) can take a while. Value is in milliseconds.
            _client = genai.Client(api_key=key, http_options=types.HttpOptions(api_version="v1beta", timeout=300_000))
        return _client


def _reset_gemini() -> None:
    """Drops the shared client so the next gemini() builds a fresh one (after "client has been closed")."""
    global _client
    with _client_lock:
        _client = None


class RateLimiter:
    """Spaces calls so at most `per_minute` start in any minute (thread-safe)."""

    def __init__(self, per_minute: int):
        self.interval = 60.0 / per_minute
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            start = max(now, self.next_at)
            self.next_at = start + self.interval
        if start > now:
            time.sleep(start - now)


def with_retry(fn, *, attempts: int = 6, what: str = "Gemini call"):
    """Retries 429 (rate/quota per minute) and 5xx with the server's retryDelay or exponential backoff.
    Daily-quota or billing errors still fail after the attempts are used up, so the stage waits for the owner."""
    delay = 4.0
    for i in range(attempts):
        try:
            return fn()
        except RuntimeError as e:
            # Seen 16 Nov on parallel Nano Banana calls: the SDK's HTTP client was found closed. Rebuild and retry.
            if "client has been closed" not in str(e) or i == attempts - 1:
                raise
            log.warning("%s: Gemini client was closed, rebuilding it and retrying (%d/%d)", what, i + 1, attempts)
            _reset_gemini()
        except (genai_errors.ClientError, genai_errors.ServerError) as e:
            code = getattr(e, "code", None) or getattr(e, "status_code", None)
            if code not in (429, 500, 503, 504) or i == attempts - 1:
                raise
            m = re.search(r"retryDelay'?:\s*'?(\d+(?:\.\d+)?)s", str(e))
            wait = max(float(m.group(1)) + 1.0, delay) if m else delay
            if wait > MAX_RETRY_WAIT:
                # 16 Nov 2026: TTS daily quota ran out and Google said "retry in 51767s"; the worker slept 14 h.
                # A wait that long means this model's quota is used up: hand over to the next model now.
                raise QuotaExhausted(f"{what}: quota used up, Google asks to wait {wait:.0f}s") from e
            log.warning("%s got %s, retrying in %.0fs (%d/%d)", what, code, wait, i + 1, attempts)
            time.sleep(wait + random.uniform(0, 1.5))
            delay = min(delay * 2, 60.0)


MAX_RETRY_WAIT = 120.0  # seconds; a longer server-requested wait is treated as an exhausted quota


class QuotaExhausted(RuntimeError):
    """A model's quota is used up for now (server asked for a very long wait)."""


def model_list(spec: str | None, default: str | None = None) -> list[str]:
    """'a,b,c' -> ['a', 'b', 'c'] (the control plane sends ordered fallback lists as comma-separated strings)."""
    models = [m.strip() for m in (spec or "").split(",") if m.strip()]
    return models or ([default] if default else [])


def _model_problem(e: Exception) -> bool:
    """Errors where trying the NEXT model makes sense: retired/unknown model, still overloaded after retries, hangs."""
    text = str(e).lower()
    if isinstance(e, (TimeoutError,)) or "timed out" in text or "timeout" in text:
        return True
    if isinstance(e, QuotaExhausted):  # each model has its own daily quota on the same account
        return True
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    if code in (404, 503, 504):
        return True
    return any(s in text for s in ("not found", "no longer available", "not supported", "deprecated"))


def with_models(spec: str | None, call, *, what: str, default: str | None = None):
    """Try each model of an ordered fallback list (Work X). Returns (model_used, result). Each model gets the
    normal 429/5xx retries first; quota/billing/content errors are NOT model problems and fail immediately."""
    models = model_list(spec, default)
    if not models:
        raise ValueError(f"{what}: no model configured")
    for i, m in enumerate(models):
        try:
            return m, with_retry(lambda: call(m), what=f"{what} ({m})")
        except Exception as e:  # noqa: BLE001 - decide below whether the next model should be tried
            if i < len(models) - 1 and _model_problem(e):
                log.warning("%s: model %s failed (%s), falling back to %s", what, m, str(e)[:160], models[i + 1])
                continue
            raise


def usage_dict(response) -> dict:
    u = getattr(response, "usage_metadata", None)
    if u is None:
        return {}
    return {
        "promptTokens": u.prompt_token_count or 0,
        "outputTokens": u.candidates_token_count or 0,
        "thoughtTokens": getattr(u, "thoughts_token_count", 0) or 0,
        "totalTokens": u.total_token_count or 0,
    }


# ----------------------------------------------------------------------------------------------------------- audio / ffmpeg

def ffmpeg_bin() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    # winget installs FFmpeg under the user's packages folder; new shells get it on PATH, old ones may not.
    root = Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    for p in root.glob("Gyan.FFmpeg*/**/bin/ffmpeg.exe"):
        return str(p)
    raise RuntimeError("ffmpeg not found")


def ffprobe_bin() -> str:
    return str(Path(ffmpeg_bin()).with_name("ffprobe" + Path(ffmpeg_bin()).suffix))


def run_ffmpeg(args: list[str]) -> None:
    proc = subprocess.run([ffmpeg_bin(), "-hide_banner", "-y", *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + proc.stderr[-2000:])


def media_duration(path: Path) -> float:
    out = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def pcm_to_wav(pcm: bytes, rate: int = 24000, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def workdir(prefix: str) -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory(prefix=f"projectv-{prefix}-")
