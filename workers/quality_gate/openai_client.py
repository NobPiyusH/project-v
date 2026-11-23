"""Minimal OpenAI Chat Completions client for one image + one prompt (httpx; the key is read from .env, never logged)."""
from __future__ import annotations

import base64
import subprocess
import time
from pathlib import Path

import httpx

from . import config

URL = "https://api.openai.com/v1/chat/completions"


def shrink_jpeg(path: Path, max_side: int, ffmpeg: str = "ffmpeg") -> bytes:
    """Downscale a picture (or the middle frame of a clip) to a JPEG no larger than max_side."""
    vf = f"scale='if(gt(iw,ih),min({max_side},iw),-2)':'if(gt(iw,ih),-2,min({max_side},ih))'"
    return subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-vf", vf, "-frames:v", "1", "-q:v", "3",
                           "-f", "image2pipe", "-vcodec", "mjpeg", "-"], capture_output=True, check=True).stdout


def make_ask(cfg: dict, timeout: float = 90.0):
    v = cfg["vision"]
    key = config.openai_key()

    def ask(jpeg: bytes, prompt: str) -> tuple[str, dict]:
        b64 = base64.b64encode(jpeg).decode()
        body = {"model": v["model"], "max_completion_tokens": v["max_output_tokens"],
                "reasoning_effort": v.get("reasoning_effort", "low"),
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": "You are a strict picture editor. Reply with JSON only."},
                             {"role": "user", "content": [
                                 {"type": "text", "text": prompt},
                                 {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": v["detail"]}}]}]}
        r = None
        for attempt in range(3):
            r = httpx.post(URL, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
            if r.status_code in (429, 500, 502, 503) and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            break
        if r.status_code != 200:
            raise RuntimeError(f"OpenAI HTTP {r.status_code}: {r.text[:300].replace(key, '***')}")
        d = r.json()
        u = d.get("usage", {})
        return d["choices"][0]["message"].get("content") or "", {"input_tokens": u.get("prompt_tokens", 0),
                                                                  "output_tokens": u.get("completion_tokens", 0)}
    return ask
