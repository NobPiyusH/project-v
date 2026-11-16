"""Researcher: Gemini with Google Search grounding -> facts, each tied to the web sources that support it."""

from __future__ import annotations

import re

from google.genai import types
from temporalio import activity

from workers.common import gemini, usage_dict, with_retry


@activity.defn(name="research_topic")
def research_topic(req: dict) -> dict:
    """req: {topic, summary, mustInclude[], language, maxFacts, model, ownerNotes}
    -> {facts:[{text, sources:[{title, uri}]}], sources:[{title, uri}], queries[], model, usage}"""
    must = ", ".join(req.get("mustInclude") or []) or "none"
    notes = req.get("ownerNotes") or ""
    prompt = f"""Research this topic for a documentary video script. Use Google Search.
Topic: {req['topic']}
Video summary: {req.get('summary', '')}
Must include: {must}
{('Owner notes: ' + notes) if notes else ''}

Write {req.get('maxFacts', 15)} concise, verifiable facts, one per line, each starting with "- ".
Prefer surprising, visual, emotionally powerful facts with concrete numbers, scales and time spans.
Only state facts that your search results support. No introduction, no conclusion."""

    response = with_retry(lambda: gemini().models.generate_content(
        model=req["model"],
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.3,
        ),
    ), what="research")
    text = response.text or ""
    cand = response.candidates[0] if response.candidates else None
    meta = getattr(cand, "grounding_metadata", None) if cand else None
    chunks = []
    if meta and meta.grounding_chunks:
        for ch in meta.grounding_chunks:
            web = getattr(ch, "web", None)
            if web and web.uri:
                chunks.append({"title": web.title or web.uri, "uri": web.uri})

    # Map each fact line to the grounding chunks whose supported segments fall inside it.
    lines = [m for m in re.finditer(r"^- (.+)$", text, flags=re.MULTILINE)]
    facts = []
    for m in lines:
        start, end = m.start(1), m.end(1)
        idx: set[int] = set()
        if meta and meta.grounding_supports:
            for sup in meta.grounding_supports:
                seg = sup.segment
                if seg is None or sup.grounding_chunk_indices is None:
                    continue
                s, e = seg.start_index or 0, seg.end_index or 0
                # Segment indices are byte offsets in the UTF-8 text; compare in bytes.
                bs, be = len(text[:start].encode()), len(text[:end].encode())
                if s < be and e > bs:
                    idx.update(sup.grounding_chunk_indices)
        facts.append({"text": m.group(1).strip(), "sources": [chunks[i] for i in sorted(idx) if i < len(chunks)]})

    return {
        "facts": facts,
        "sources": chunks,
        "queries": list(meta.web_search_queries or []) if meta else [],
        "model": req["model"],
        "usage": usage_dict(response),
    }
