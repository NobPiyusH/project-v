"""Source check (18 Nov 2026): after research, read each fact's cited page and confirm that the fact's key numbers
appear there. Facts are marked confirmed / unconfirmed / unreachable; the writer is told not to state unconfirmed
numbers as fact. Free, and it never blocks a video.

Polite by design (CLAUDE.md rule 4): plain HTTP with an honest User-Agent, robots.txt respected, one request per
second per site, small page and time limits. No stealth or anti-bot features: Scrapling is used only as an HTML
parser (its core, BSD-3); its optional "fetchers" extra is deliberately not installed.
"""

from __future__ import annotations

import re
import time
import urllib.parse
import urllib.request
import urllib.robotparser

from temporalio import activity

from workers.common import log

UA = "projectv-studio/1.0 (private documentary tool; source check)"
MAX_PAGES = 30
MAX_BYTES = 3_000_000
NUMBER = re.compile(r"\d[\d,.]*\d|\d")
_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
_last_hit: dict[str, float] = {}


def key_numbers(text: str) -> list[str]:
    """Numbers a reader would remember ("4,000", "27", "90"), normalised; years and tiny numbers kept too."""
    out = []
    for m in NUMBER.findall(text):
        n = m.replace(",", "").rstrip(".")
        if n and n not in out:
            out.append(n)
    return out


def _norm_page(text: str) -> str:
    return re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)  # "4,000" -> "4000" so numbers compare in one form


def _allowed(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    root = f"{parts.scheme}://{parts.netloc}"
    if root not in _robots:
        rp = urllib.robotparser.RobotFileParser(root + "/robots.txt")
        try:
            req = urllib.request.Request(root + "/robots.txt", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=10) as r:
                rp.parse(r.read().decode("utf-8", "replace").splitlines())
        except Exception:  # noqa: BLE001 - no robots.txt: allowed
            rp = None
        _robots[root] = rp
    rp = _robots[root]
    return rp is None or rp.can_fetch(UA, url)


def page_text(url: str) -> str | None:
    """Visible text of a page, or None when not allowed / not reachable / not HTML."""
    from scrapling.parser import Selector  # core parser only (no fetchers, no stealth)

    if not url.startswith(("http://", "https://")) or not _allowed(url):
        return None
    host = urllib.parse.urlsplit(url).netloc
    wait = 1.0 - (time.time() - _last_hit.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    _last_hit[host] = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=15) as r:
            if "html" not in (r.headers.get("Content-Type") or ""):
                return None
            raw = r.read(MAX_BYTES).decode(r.headers.get_content_charset() or "utf-8", "replace")
    except Exception:  # noqa: BLE001
        return None
    page = Selector(raw)
    return " ".join(t.strip() for t in page.css("body *::text").getall() if t.strip())


def check(facts: list[dict]) -> list[dict]:
    """Adds "check" to each fact: confirmed (all its numbers on a cited page), unconfirmed, unreachable, or
    no-numbers (nothing to check)."""
    cache: dict[str, str | None] = {}
    pages = 0
    for f in facts:
        nums = key_numbers(f.get("text") or "")
        if not nums:
            f["check"] = "no-numbers"
            continue
        seen_page, ok = False, False
        for s in (f.get("sources") or [])[:2]:
            url = s.get("uri") or ""
            if url not in cache:
                if pages >= MAX_PAGES:
                    continue
                pages += 1
                cache[url] = page_text(url)
            text = cache[url]
            if text is None:
                continue
            seen_page = True
            body = _norm_page(text)
            if all(n in body for n in nums):
                ok = True
                break
        f["check"] = "confirmed" if ok else ("unconfirmed" if seen_page else "unreachable")
    return facts


@activity.defn(name="check_sources")
def check_sources(research: dict) -> dict:
    """ResearchResult in, the same with facts[i].check filled in. Never raises: a failed check leaves facts as they are."""
    try:
        check(research.get("facts") or [])
        counts = {}
        for f in research.get("facts") or []:
            counts[f.get("check")] = counts.get(f.get("check"), 0) + 1
        log.info("Source check: %s", counts)
    except Exception as e:  # noqa: BLE001
        log.warning("Source check skipped (%s)", type(e).__name__)
    return research
