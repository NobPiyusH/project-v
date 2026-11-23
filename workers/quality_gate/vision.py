"""Check 1: does the picture match the shot? Pure parts (prompt, answer parsing, verdict) + a runner that takes any
`ask(image_bytes, prompt) -> (text, usage)` callable, so tests need no network."""
from __future__ import annotations

import json
import re
from collections.abc import Callable

from . import FAIL, INFO, WARN, CheckResult, Finding
from .ledger import CapExceeded, Ledger, call_cost_usd, estimate

SCHEMA_HINT = ('{"match": 1-5, "problems": [short strings], "wrong_text": [strings], "cut_off_text": true|false, '
               '"summary": "one sentence of what the picture shows"}')


def build_prompt(description: str, narration: str = "", overlay_text: str = "", kind: str = "keyframe") -> str:
    parts = [f"Shot description (what the picture should show): {description.strip()}"]
    if narration:
        parts.append(f"Narration line spoken over this shot: {narration.strip()}")
    if overlay_text:
        parts.append(f"On-screen text that will be added later (not expected in the picture): {overlay_text.strip()}")
    what = {"stock": "stock video frame", "diagram": "frame of an animated explainer diagram"}.get(kind, "generated picture")
    extra = (" For a diagram also report overlapping labels, labels over shapes they do not belong to, and labels cut off at the "
             "frame edge in problems, and misspelled or implausible labels in wrong_text.") if kind == "diagram" else ""
    parts.append(
        f"This is a {what}. Score how well it matches the description and narration: 5 = exactly right, "
        "4 = right with minor differences, 3 = acceptable but loosely related, 2 = wrong subject or wrong "
        "era/technology, 1 = unrelated. List concrete problems (wrong object, anachronism such as a flat screen where "
        "a CRT tube is described, warped hands or faces, garbled or misspelled lettering). Put any visible lettering "
        "that looks wrong in wrong_text and set cut_off_text true if text touches or crosses the frame edge." + extra + " "
        f"Answer as JSON only: {SCHEMA_HINT}")
    return "\n".join(parts)


def parse_answer(text: str) -> dict | None:
    """Pulls the JSON object out of the model's reply; None when it is not usable."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        d["match"] = int(d["match"])
    except (ValueError, KeyError, TypeError):
        return None
    d["problems"] = [str(p) for p in (d.get("problems") or [])]
    d["wrong_text"] = [str(p) for p in (d.get("wrong_text") or [])]
    d["cut_off_text"] = bool(d.get("cut_off_text"))
    return d


def verdict(ans: dict, pass_score: int, ref: str, name: str = "picture") -> list[Finding]:
    out: list[Finding] = []
    score = ans["match"]
    if score < pass_score:
        out.append(Finding("vision", FAIL, "VISION_MISMATCH",
                           f"{name} scores {score}/5 against its shot: {'; '.join(ans['problems']) or ans.get('summary', '')}", ref))
    elif ans["problems"]:
        out.append(Finding("vision", WARN, "VISION_PROBLEM", f"{name} scores {score}/5; " + "; ".join(ans["problems"]), ref))
    if ans["wrong_text"]:
        out.append(Finding("vision", WARN, "VISION_WRONG_TEXT", "Lettering looks wrong: " + "; ".join(ans["wrong_text"]), ref))
    if ans["cut_off_text"]:
        out.append(Finding("vision", WARN, "VISION_CUT_OFF_TEXT", "Text touches or crosses the frame edge.", ref))
    return out


def run(items: list[dict], ask: Callable[[bytes, str], tuple[str, dict]], ledger: Ledger, cfg: dict,
        load_image: Callable[[dict], bytes]) -> CheckResult:
    """items: [{ref, kind, description, narration, overlay_text}]. One paid call per item; stops cleanly at a cap."""
    res = CheckResult("vision")
    v = cfg["vision"]
    worst = estimate(1, cfg)["per_call_worst_usd"]
    results = []
    for it in items:
        try:
            ledger.reserve(worst)
        except CapExceeded as e:
            res.findings.append(Finding("vision", WARN, "VISION_CAP_STOP", f"Stopped before {it['ref']}: {e}", it["ref"]))
            if not results:
                res.skipped = str(e)
            break
        prompt = build_prompt(it["description"], it.get("narration", ""), it.get("overlay_text", ""), it.get("kind", "keyframe"))
        text, usage = ask(load_image(it), prompt)
        usd = call_cost_usd(usage.get("input_tokens", 0), usage.get("output_tokens", 0), v["price_usd_per_1m"])
        ledger.record(usd, usage.get("input_tokens", 0) + usage.get("output_tokens", 0), f"vision check {it['ref']} ({v['model']})")
        ans = parse_answer(text)
        if ans is None:
            res.findings.append(Finding("vision", WARN, "VISION_UNREADABLE", "The model's answer could not be read; shot not judged.", it["ref"]))
            continue
        results.append({"ref": it["ref"], "score": ans["match"], "summary": ans.get("summary", ""), "problems": ans["problems"],
                        "cost_usd": round(usd, 5)})
        res.findings += verdict(ans, v["pass_score"], it["ref"], it.get("kind", "picture"))
    res.stats = {"judged": len(results), "results": results, "spent_usd": round(ledger.run_usd, 5)}
    if results and not any(f.severity != INFO for f in res.findings):
        res.findings.append(Finding("vision", INFO, "VISION_OK", f"{len(results)} pictures judged, none below {v['pass_score']}/5."))
    return res
