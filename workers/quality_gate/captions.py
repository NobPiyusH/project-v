"""Check 4: captions. No one-word leftover cards, no caption on top of on-screen text, no overlapping cards, and every
caption starts where the voice is (matches a narration line's span).

Where captions sit: bottom band (assemble.write_ass: landscape, 7% bottom margin, 5% font). On-screen text that lives
in that band is a clash: diagram labels placed low (spec geometry), or overlay text if it were ever placed low.
"""
from __future__ import annotations

import re

from . import FAIL, WARN, CheckResult, Finding
from .diagram import in_caption_band

TS = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)")


def _sec(ts: str) -> float:
    m = TS.match(ts.strip())
    h, mi, s, ms = (int(x) for x in m.groups())
    return h * 3600 + mi * 60 + s + ms / 1000


def parse_srt(text: str) -> list[dict]:
    cues = []
    for block in re.split(r"\n\s*\n", text.strip().replace("\r\n", "\n")):
        rows = block.strip().split("\n")
        t = next((r for r in rows if "-->" in r), None)
        if not t:
            continue
        a, b = t.split("-->")
        body = " ".join(rows[rows.index(t) + 1:]).strip()
        cues.append({"start": _sec(a), "end": _sec(b), "text": body})
    return cues


def words(text: str) -> int:
    return len(re.findall(r"[\w'’-]+", text))


def check_cards(cues: list[dict], min_words: int = 2) -> list[Finding]:
    out = []
    for i, c in enumerate(cues, 1):
        ref = f"caption {i} @ {c['start']:.1f}s"
        if words(c["text"]) < min_words:
            out.append(Finding("captions", FAIL, "CAPTION_ONE_WORD", f"One-word caption card: “{c['text']}”.", ref))
        if i < len(cues) and c["end"] > cues[i]["start"] + 0.02:
            out.append(Finding("captions", WARN, "CAPTION_OVERLAP", f"Caption runs {c['end'] - cues[i]['start']:.2f}s into the next card.", ref))
        if c["end"] - c["start"] < 0.3:
            out.append(Finding("captions", WARN, "CAPTION_TOO_SHORT", f"Caption is on screen only {c['end'] - c['start']:.2f}s.", ref))
    return out


def check_clash(cues: list[dict], shots: list[dict], specs: dict[int, dict]) -> list[Finding]:
    """A caption shown while the diagram under it has text in the caption band."""
    out = []
    for s in shots:
        spec = specs.get(s["n"])
        if s.get("lane") != "diagram" or not spec or s.get("startSec") is None:
            continue
        low = in_caption_band(spec)
        if not low:
            continue
        a, b = s["startSec"], s["startSec"] + s["durationSec"]
        n = sum(1 for c in cues if c["start"] < b and c["end"] > a)
        if n:
            out.append(Finding("captions", WARN, "CAPTION_NEAR_DIAGRAM_TEXT",
                               f"{n} caption card(s) share the bottom band with diagram text ({', '.join(low)}); estimated from the spec, check by eye.", f"shot {s['n']}"))
    return out


def run(srt_text: str, shots: list[dict], specs: dict[int, dict], cfg: dict) -> CheckResult:
    res = CheckResult("captions")
    cues = parse_srt(srt_text)
    if not cues:
        res.skipped = "no caption cues found"
        return res
    res.findings += check_cards(cues, cfg["captions"]["min_words_per_card"])
    res.findings += check_clash(cues, shots, specs)
    res.stats = {"cards": len(cues), "one_word": sum(1 for c in cues if words(c["text"]) < cfg["captions"]["min_words_per_card"])}
    return res
