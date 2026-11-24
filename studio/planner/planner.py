"""R1 Planner: free-text prompt -> VideoPlan. Port of Planner.

Uses Claude first (via Llm), then Gemini, when a key is configured; otherwise (or if the call fails) a rule-based
fallback, so the pipeline never blocks on the LLM.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from studio.planner.plan import VideoPlan
from studio.planner.presets import StylePresets
from studio.workflow.stage import Stage

log = logging.getLogger(__name__)

VISUAL_TYPES = ["ai_video", "ai_image_motion", "motion_graphics", "stock_footage", "text_cards", "data_counter"]
_DURATION = re.compile(r"(\d{1,3})[\s-]*(seconds?|secs?|s|minutes?|mins?|m)\b", re.IGNORECASE)


@dataclass
class Outcome:
    plan: VideoPlan
    stages: list[Stage]
    source: str  # how the plan was produced: rules | claude:... | gemini:... | aicredits
    total_tokens: int | None = None
    fallback_reason: str | None = None


def _one_of(value: str | None, allowed: list[str], default: str) -> str:
    return value if value is not None and value in allowed else default


def _blank_to(value: str | None, default: str) -> str:
    return default if value is None or not value.strip() else value


def _short_title(prompt: str) -> str:
    t = re.sub(r"\s+", " ", prompt.strip())
    return t if len(t) <= 70 else t[:67] + "..."


def stages_for(plan: VideoPlan) -> list[Stage]:
    """Which stages this plan needs. PLAN itself always runs."""
    stages = list(Stage)
    if not plan.needsResearch:
        stages.remove(Stage.RESEARCH)
    if plan.music == "none":
        stages.remove(Stage.MUSIC)
    if plan.narration == "none":
        stages.remove(Stage.NARRATION)
    return stages


class Planner:
    def __init__(self, presets: StylePresets, llm, api_key: str, model_name: str, story_model: str):
        self._presets = presets
        self._llm = llm  # None when no chat model is available
        self._api_key = api_key
        self._model_name = model_name
        self._story_model = story_model

    def llm_configured(self) -> bool:
        return bool(self._api_key and self._api_key.strip()) and self._llm is not None

    def model_name(self) -> str:
        return self._model_name

    def plan(self, video_id, prompt: str, owner_notes: str | None) -> Outcome:
        if not self.llm_configured():
            return self.fallback(prompt, "No GEMINI_API_KEY configured")
        try:
            # Llm tries Claude first (when keyed and under its monthly cap), then the Gemini list, with the time limit,
            # budget checks, AICredits fallback and the cost ledger entry all handled there. Rules are the last resort.
            answer = self._llm.call_with_model(
                video_id, "PLAN", self._system_prompt(), self._user_prompt(prompt, owner_notes), VideoPlan, self._story_model
            )
            used = answer.model
            source = used if ":" in used or used == "aicredits" else f"gemini:{used}"
            plan = self.normalise(answer.entity, prompt)
            return Outcome(plan, stages_for(plan), source)
        except Exception as e:
            log.warning("Planning failed, using rule-based fallback: %r", e)  # the client's message never contains the key
            return self.fallback(prompt, f"Planning call failed: {type(e).__name__}")

    def _system_prompt(self) -> str:
        head = f"""You are the Planner of an AI video studio. From the user's prompt, decide everything the video needs.
Return ONLY the JSON object for the plan. Rules:
- format: "long_form" or "short". Shorts/Reels are vertical 9:16 and 15-60 seconds.
- durationSec: respect any length in the prompt; otherwise use the chosen preset's default.
- aspectRatio: "16:9" for long_form, "9:16" for short, unless the prompt says otherwise.
- outputResolution: "2160p" for long_form, "1080p" for short.
- stylePreset: one of the preset ids below; pick the best fit for the prompt.
- language: ISO 639-1 code of the narration language the user wants (default "en").
- needsResearch: true when the video makes factual claims (science, history, tech, finance, how-things-work).
- narration: "full", "sparse" or "none". voice: a short description of the ideal narrator voice, or "" when none.
- music: "lead" (edit is cut to the music), "support" or "none". soundDesign: true unless clearly unwanted.
- visualTypes: choose from [{", ".join(VISUAL_TYPES)}]. Prefer real moving footage (ai_video); never plan a slideshow of panned stills.
- shortsCount: how many 30-60 s vertical cut-downs to make (0 for a short; usually 3-5 for long_form).
- mustInclude: concrete items the user explicitly asked for.
- title: a strong working title. summary: 1-2 sentences. rationale: 1-2 sentences on why these choices.
Style presets:
"""
        lines = [f"- {p.id} ({p.name}): {p.description} Defaults: {p.defaults}\n" for p in self._presets.all()]
        return head + "".join(lines)

    @staticmethod
    def _user_prompt(prompt: str, owner_notes: str | None) -> str:
        base = "Prompt:\n" + prompt
        if owner_notes and owner_notes.strip():
            base += "\n\nThe owner rejected the previous plan with these notes; follow them:\n" + owner_notes
        return base

    def normalise(self, p: VideoPlan, prompt: str) -> VideoPlan:
        """Fill gaps and clamp values so later stages can trust the plan regardless of where it came from."""
        preset = self._presets.by_id_or_default(p.stylePreset)
        d = preset.defaults
        fmt = _one_of(p.format, ["long_form", "short"], str(d.get("format", "long_form")))
        is_short = fmt == "short"
        duration = p.durationSec if p.durationSec > 0 else int(d.get("durationSec", 480))
        duration = max(10, min(duration, 90)) if is_short else max(30, min(duration, 3600))
        visuals = [v for v in (p.visualTypes or []) if v in VISUAL_TYPES] or ["ai_video"]
        narration = _one_of(p.narration, ["full", "sparse", "none"], str(d.get("narration", "full")))
        return VideoPlan(
            title=_blank_to(p.title, _short_title(prompt)),
            summary=_blank_to(p.summary, prompt),
            format=fmt,
            durationSec=duration,
            aspectRatio=_one_of(p.aspectRatio, ["16:9", "9:16", "1:1", "4:5"], "9:16" if is_short else "16:9"),
            outputResolution=_one_of(p.outputResolution, ["2160p", "1080p"], "1080p" if is_short else "2160p"),
            stylePreset=preset.id,
            tone=_blank_to(p.tone, "engaging"),
            language=_blank_to(p.language, "en").lower(),
            needsResearch=p.needsResearch,
            narration=narration,
            voice="" if narration == "none" else _blank_to(p.voice, "warm, clear documentary narrator"),
            music=_one_of(p.music, ["lead", "support", "none"], str(d.get("music", "support"))),
            soundDesign=p.soundDesign,
            visualTypes=visuals,
            shortsCount=0 if is_short else max(0, min(p.shortsCount, 10)),
            mustInclude=p.mustInclude or [],
            rationale=_blank_to(p.rationale, ""),
        )

    def fallback(self, prompt: str, reason: str) -> Outcome:
        """Rule-based plan, used when there is no key or the LLM call fails."""
        p = prompt.lower()
        if any(w in p for w in ("short", "reel", "tiktok", "vertical")):
            preset = "shorts_punchy"
        elif any(w in p for w in ("ambient", "relax", "no narration", "calm", "sleep", "meditat")):
            preset = "ambient_visual"
        elif any(w in p for w in ("timelapse", "time-lapse", "universe", "cosmos", "million years", "billion years")):
            preset = "cinematic_awe"
        elif any(w in p for w in ("how ", "explain", "what is", "why ")):
            preset = "explainer_clean"
        else:
            preset = "documentary_story"
        duration = 0
        m = _DURATION.search(p)
        if m:
            n = int(m.group(1))
            duration = n * 60 if m.group(2).lower().startswith("m") else n
        language = "hi" if "hindi" in p else "ta" if "tamil" in p else "es" if "spanish" in p else "en"
        raw = VideoPlan(
            title=_short_title(prompt), summary=prompt, durationSec=duration, stylePreset=preset, language=language,
            needsResearch=preset != "ambient_visual", soundDesign=True, visualTypes=["ai_video", "motion_graphics"],
            shortsCount=0 if preset == "shorts_punchy" else 3, mustInclude=[],
            rationale=f"Rule-based plan ({reason}). Connect Gemini for a smarter plan.",
        )
        plan = self.normalise(raw, prompt)
        return Outcome(plan, stages_for(plan), "rules", None, reason)
