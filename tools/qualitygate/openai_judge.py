"""OpenAI backend for the Tier 3 judge. OFF unless a caller builds it; nothing here runs at import time.

Safety rails (CLAUDE.md rules 2, 3, 6, 7):
- a per-run spend cap is REQUIRED (`max_usd`, no default) and is checked BEFORE every call against the worst case; the shared gate ledger
  (workers/quality_gate/ledger.py) also enforces its per-day cap and writes one cost row per call;
- the key is read lazily from .env through workers.quality_gate.config.openai_key() when a request is actually sent, and is never logged;
- model, image size and prices come from config/quality-gate.yaml (re-verify prices before changing, rule 7);
- every call logs model, tokens and USD (logger `qualitygate.judge`).
Tests inject `post` (the HTTP call) and `read_jpeg` (frame loading), so no network and no key are needed.
"""

from __future__ import annotations

import base64
import copy
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .tier3 import Judge, JudgePrompt, JudgeUnavailable

log = logging.getLogger("qualitygate.judge")
URL = "https://api.openai.com/v1/chat/completions"
PROMPT_OVERHEAD_TOKENS = 800  # rubric text + reply format, a deliberate over-estimate for the pre-call worst case
SYSTEM = "You are a strict video QA reviewer. Reply with JSON only."

Post = Callable[..., Any]  # httpx.post-compatible: post(url, json=, headers=, timeout=) -> response with .status_code/.json()/.text


def worst_case_usd(n_frames: int, cfg: dict, width: int = 1024, height: int = 576) -> float:
    """Most one judge call can cost: all images + prompt in, the full output budget out."""
    from workers.quality_gate.ledger import call_cost_usd, image_tokens

    v = cfg["vision"]
    tokens_in = n_frames * image_tokens(width, height, v["patch_multiplier"]) + PROMPT_OVERHEAD_TOKENS
    return call_cost_usd(tokens_in, v["max_output_tokens"], v["price_usd_per_1m"])


def estimate_run(n_shots: int, frames_per_shot: int, cfg: dict) -> dict:
    """What a run could cost, shown to the owner BEFORE any paid call."""
    worst = worst_case_usd(frames_per_shot, cfg)
    fx = float(cfg["caps"]["fx_inr_per_usd"])
    return {"shots": n_shots, "frames_per_shot": frames_per_shot, "worst_per_shot_usd": round(worst, 5),
            "worst_run_usd": round(worst * n_shots, 4), "worst_run_inr": round(worst * n_shots * fx, 2),
            "model": cfg["vision"]["model"]}  # fmt: skip


def build_body(prompt: JudgePrompt, jpegs: list[bytes], cfg: dict) -> dict:
    v = cfg["vision"]
    content: list[dict] = [{"type": "text", "text": prompt.text}]
    for jpeg in jpegs:
        b64 = base64.b64encode(jpeg).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": v["detail"]}})
    return {"model": v["model"], "max_completion_tokens": v["max_output_tokens"], "reasoning_effort": v.get("reasoning_effort", "low"),
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}]}  # fmt: skip


def _default_read_jpeg(max_side: int) -> Callable[[str], bytes]:
    from workers.quality_gate.openai_client import shrink_jpeg

    return lambda path: shrink_jpeg(Path(path), max_side)


def _default_post() -> Post:
    import httpx

    return httpx.post


def send(body: dict, key: str, post: Post, timeout: float = 120.0) -> tuple[str, dict]:
    """POST with up to 3 tries on 429/5xx. Returns (reply text, usage). The key is scrubbed from any error text."""
    r = None
    for attempt in range(3):
        r = post(URL, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
        if r.status_code in (429, 500, 502, 503) and attempt < 2:
            time.sleep(2 * (attempt + 1))
            continue
        break
    if r.status_code != 200:
        raise JudgeUnavailable(f"OpenAI HTTP {r.status_code}: {r.text[:200].replace(key, '***')}")
    d = r.json()
    u = d.get("usage", {})
    text = (d.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    return text, {"input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0)}


def make_openai_judge(max_usd: float, ledger: Any | None = None, cfg: dict | None = None, post: Post | None = None,
                      read_jpeg: Callable[[str], bytes] | None = None, key_fn: Callable[[], str] | None = None,
                      video_id: str | None = None) -> Judge:  # fmt: skip
    """Build the judge callable. `max_usd` (> 0) is the owner's per-run cap and is mandatory: there is no default and no 'unlimited'."""
    if not max_usd or max_usd <= 0:
        raise ValueError("a per-run spend cap (max_usd > 0) is required for the OpenAI judge")
    from workers.quality_gate import config as gate_config
    from workers.quality_gate.ledger import CapExceeded, Ledger, call_cost_usd

    base = cfg or gate_config.load()
    run_cfg = copy.deepcopy(base)
    run_cfg["caps"]["per_run_usd"] = min(float(max_usd), float(run_cfg["caps"]["per_run_usd"]))  # never above the repo-wide cap
    led = ledger or Ledger(Path(run_cfg["stock_judge"]["ledger"]), run_cfg, video_id)
    led.cfg = run_cfg
    reader = read_jpeg or _default_read_jpeg(int(run_cfg["vision"]["max_side_px"]))
    do_post = post
    price = run_cfg["vision"]["price_usd_per_1m"]

    def judge(prompt: JudgePrompt) -> str:
        nonlocal do_post
        try:
            led.reserve(worst_case_usd(len(prompt.frames), run_cfg))
        except CapExceeded as e:
            log.warning("judge skipped for %s: %s", prompt.shot_id, e)
            raise JudgeUnavailable(f"spend cap: {e}") from e
        jpegs = [reader(f.path) for f in prompt.frames]
        key = (key_fn or gate_config.openai_key)()
        do_post = do_post or _default_post()
        text, usage = send(build_body(prompt, jpegs, run_cfg), key, do_post)
        usd = call_cost_usd(usage["input_tokens"], usage["output_tokens"], price)
        led.record(usd, usage["input_tokens"] + usage["output_tokens"], f"qualitygate judge {prompt.shot_id} ({run_cfg['vision']['model']})")
        log.info("judge %s: model=%s in=%d out=%d cost=$%.5f run_total=$%.5f cap=$%.2f", prompt.shot_id, run_cfg["vision"]["model"],
                 usage["input_tokens"], usage["output_tokens"], usd, led.run_usd, run_cfg["caps"]["per_run_usd"])
        return text

    return judge
