"""Concept and script (HANDOVER §10: Writer). Narration length is derived from the plan's duration and style. Port of WriterAgent."""

from __future__ import annotations

from studio.agents.jfmt import jround
from studio.agents.types import Concept, Script
from studio.media.types import Fact
from studio.planner.plan import VideoPlan
from studio.planner.presets import StylePresets

# Gemini TTS voices with their character (Google's own one-word descriptions), so the writer casts by meaning:
# the voice suits the story's context and depth (owner, 18 Nov 2026). Verify the list when the TTS model changes.
VOICE_CHARACTER: dict[str, str] = {
    "Charon": "informative, calm, deep", "Kore": "firm, clear", "Orus": "firm", "Iapetus": "clear", "Algieba": "smooth",
    "Gacrux": "mature", "Sadaltager": "knowledgeable", "Enceladus": "breathy, intimate", "Algenib": "gravelly",
    "Schedar": "even", "Rasalgethi": "informative", "Achird": "friendly", "Fenrir": "excitable", "Puck": "upbeat",
    "Zephyr": "bright", "Aoede": "breezy, soft (owner liked it, 18 Nov 2026)", "Sulafat": "warm", "Vindemiatrix": "gentle",
    "Achernar": "soft", "Despina": "smooth",
}
VOICES = list(VOICE_CHARACTER)


def blank(s: str | None) -> bool:
    return s is None or not s.strip()


def facts_text(facts: list[Fact] | None) -> str:
    if not facts:
        return "(no research for this video)"
    # Facts whose numbers were not found on their cited page are marked so the writer treats them with care.
    return "\n".join(
        "- " + f.text + (" [numbers not confirmed by the source page: avoid stating them, or soften]" if f.check == "unconfirmed" else "")
        for f in facts
    )


def notes(owner_notes: str | None) -> str:
    if blank(owner_notes):
        return ""
    # Notes from the Inspector / Creative Director already say who rejected what and why.
    if owner_notes.startswith("The Inspector"):
        return owner_notes + "\n"
    return "The owner rejected the previous version with these notes; follow them exactly:\n" + owner_notes


class WriterAgent:
    def __init__(self, llm, presets: StylePresets, story_model: str = "", script_model: str = ""):
        self._llm, self._presets, self._story_model = llm, presets, story_model
        # Model tried first for the script itself (owner A/B, 23 Nov 2026: OpenAI scripts preferred); blank means the story model.
        self._script_model = script_model if script_model and script_model.strip() else story_model

    def concept(self, video_id, prompt: str, plan: VideoPlan, facts: list[Fact] | None, owner_notes: str | None) -> Concept:
        preset = self._presets.by_id_or_default(plan.stylePreset)
        system = f"""You are the head writer of a premium documentary studio. Style preset: {preset.name} ({preset.description}).
Design the concept for this video: one big idea that makes the audience feel something, an optional
on-screen "clock" (a counter such as years, distance or temperature that drives the narrative; leave
clockLabel empty if it does not fit), and 3-6 acts whose approxSec add up to the target duration.
Return only the JSON object."""
        user = f"""Owner's prompt: {prompt}
Plan: {plan.title}, {plan.durationSec} seconds, tone {plan.tone}, language {plan.language}, narration {plan.narration}.
Must include: {", ".join(plan.mustInclude or [])}
Research facts:
{facts_text(facts)}
{notes(owner_notes)}"""
        # Claude first: it carries the research's corrections into the acts (A/B, 17 Nov 2026: concept 9 vs 7).
        return self._llm.call(video_id, "CONCEPT", system, user, Concept, self._story_model)

    def script(self, video_id, prompt: str, plan: VideoPlan, concept: Concept, facts: list[Fact] | None, owner_notes: str | None) -> Script:
        preset = self._presets.by_id_or_default(plan.stylePreset)
        d = plan.durationSec
        if plan.narration == "none":
            length_rule = (
                f"There is NO narration. Write {max(6, d // 8)} visual beats (spoken=false), each a vivid description "
                "of what the audience sees and feels."
            )
        elif plan.narration == "sparse":
            length_rule = (
                f"Narration is SPARSE and poetic: about {jround(d * 0.75)} words in total, in {max(6, d // 20)}-{max(8, d // 12)} "
                "short lines (spoken=true), leaving long musical silences. Add a few spoken=false visual beats where music "
                "alone should carry the moment."
            )
        else:
            # ~2.1 spoken words/s for TTS narration, minus room for breaths between lines and music-only moments.
            length_rule = (
                f"Narration is FULL but must leave breathing room: at most {jround(d * 2.1 * 0.78)} words in total, split into "
                f"lines of 1-2 sentences (spoken=true). Never exceed this; the video cannot be longer than {d} seconds."
            )
        voices = "; ".join(f"{k}: {v}" for k, v in VOICE_CHARACTER.items())
        system = f"""You are the head writer of a premium documentary studio. Style preset: {preset.name} ({preset.description}).
Write the script as an ordered list of lines. {length_rule}
Every factual claim must come from the research facts provided. Write in language "{plan.language}".
Cast the narrator: pick voiceName from these voices (name: character) to suit this story's context, depth and mood: {voices}.
Describe the overall reading style in voiceStyle as a short phrase of at most 8 words (e.g. "slow, hushed and awe-struck").
Give every spoken line a delivery: at most 6 words on how to read THAT line as the story moves (e.g. "hushed, curious", "rising excitement", "slow, tender", "punchy, fast"), so the voice follows the emotional arc. Then write a one-paragraph
musicBrief for the composer (mood, instruments, tempo feel, how it should build). No sound-effect cues in lines.
Return only the JSON object."""
        acts = "" if concept.acts is None else "; ".join(f"{a.title} ({a.approxSec}s): {a.purpose}" for a in concept.acts)
        user = f"""Owner's prompt: {prompt}
Title: {plan.title}. Duration: {d} s. Tone: {plan.tone}.
Concept: {concept.logline}
Big idea: {concept.bigIdea}
Clock: {"none" if blank(concept.clockLabel) else concept.clockLabel}
Acts: {acts}
Must include: {", ".join(plan.mustInclude or [])}
Research facts:
{facts_text(facts)}
{notes(owner_notes)}"""
        return self._llm.call(video_id, "SCRIPT", system, user, Script, self._script_model)
