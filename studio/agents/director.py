"""Shot plan (HANDOVER §10: Director). Timing is refined afterwards in code (snapping to the music's beats). Port of DirectorAgent."""

from __future__ import annotations

from dataclasses import dataclass

from studio.agents.jfmt import fixed
from studio.agents.types import Concept, ShotPlan
from studio.agents.writer import blank, notes
from studio.planner.plan import VideoPlan
from studio.planner.presets import StylePresets

STOCK_RULE = """- lane may also be "stock": a REAL stock video clip (Pexels, Pixabay, Wikimedia Commons) of ordinary things that are easy to
  film: a living room, hands at a workbench, a factory floor, an antenna at sunset, a street, a family in a dim room.
  Use it for realistic everyday places, objects and people-doing-things, never for mechanisms (those are diagrams), never for
  imaginary or abstract scenes, never when a specific brand, logo, famous person or exact historical moment is needed.
  The description is the search text: say plainly what is filmed. ALWAYS also fill keyframePrompt and motionPrompt for a stock
  shot: they are used if no real clip is found.
"""

DIAGRAM_RULE = """- lane: "ai_video" (default) or "diagram". Use "diagram" for a shot whose line explains HOW something works
  (parts, flows, layers, signals, colour mixing, a process step by step): it is drawn as an animated, labelled
  diagram, accurate by construction. Never use it for atmosphere, people or places; AI video hallucinates
  mechanisms, so a mechanism shot must be "diagram". A diagram shot's keyframePrompt/motionPrompt may be empty;
  its description must say exactly what the diagram shows and in what order. Diagram shots last 5-12 seconds.
"""


@dataclass
class TimedLine:
    """A script line with its real narration length, as measured from the generated voice."""

    index: int
    text: str
    spoken: bool
    duration_sec: float


class DirectorAgent:
    def __init__(self, llm, presets: StylePresets, story_model: str = "", diagram_lane: bool = False, stock_lane: bool = False):
        self._llm, self._presets, self._story_model = llm, presets, story_model
        self._diagram_lane, self._stock_lane = diagram_lane, stock_lane  # VIDEO_DIAGRAM_LANE / VIDEO_STOCK_LANE (default off)

    def plan(self, video_id, plan: VideoPlan, concept: Concept, lines: list[TimedLine], bpm: float, total_sec: float,
             owner_notes: str | None) -> ShotPlan:
        preset = self._presets.by_id_or_default(plan.stylePreset)
        is_short = plan.format == "short"
        pacing = "fast cuts: shots of 1.5-3 seconds" if is_short else "cinematic pacing: shots of 4-9 seconds, longer for awe moments"
        beat = fixed(60.0 / bpm if bpm > 0 else 0.5, 2)
        extra = (DIAGRAM_RULE if self._diagram_lane and plan.aspectRatio == "16:9" else "") + (STOCK_RULE if self._stock_lane else "")
        system = f"""You are the director of a premium documentary studio. Style preset: {preset.name} ({preset.description}).
Break the video into an ordered list of shots covering exactly {fixed(total_sec, 0)} seconds in total, with {pacing}.
Every script line must be covered: set lineIndex on the shot(s) during which that line is heard, in order;
a spoken line's shots must together last at least its duration plus ~0.5 s. Use lineIndex null for
music-only moments. Music tempo is about {fixed(bpm, 0)} BPM: prefer durations that are multiples of one beat ({beat} s).
For each shot write:
- description: what we see, in plain words
- keyframePrompt: a rich, self-contained image prompt for the FIRST frame (subject, composition, lens,
  light, colour, atmosphere). Never mention the camera move. Never ask for text, words, letters,
  numbers, titles, signs, logos, or clock or screen digits: image and video models can't spell, so
  anything the viewer must read goes in overlayText (the edit draws it). Never depict real people.
- motionPrompt: how the shot moves over its duration (camera move + motion in the scene), for a video model
- cameraMove: one of push_in, pull_out, orbit, drift_left, drift_right, crane_up, crane_down, static, flythrough
- shotType: extreme_wide, wide, medium, close_up, macro, aerial, pov
- priority: "hero" for the 10-20% of shots that matter most (opening, reveals, climax, ending), else "standard"
- overlayText: short on-screen text such as the clock value ("13.8 billion years ago"), or empty
- footageQuery: whenever a REAL recording of this exact thing is likely to exist and would show the
  line as well as or truer than a generated image (it is free, and real footage reads as authentic):
  space (the real Sun, Earth from orbit, launches, telescope views) from NASA, or real nature, animals,
  insects, plants, weather, microscopy, lab experiments, places, historic events and expeditions (e.g.
  NOAA deep-sea dives) from Wikimedia Commons. In science videos, aim for roughly a quarter of the
  shots where it fits. 2-5 exact search words, such as "solar flare SDO", "Okeanos Explorer",
  "firefly bioluminescence". Otherwise empty. Always for a real event or "first ever" claim. Never for
  imaginary, dreamlike or abstract scenes, and never for hero shots that need a specific composition.
- sfxQuery: 2-4 words for a quiet background sound that makes the shot feel real ("underwater
  ambience", "train interior", "wind over grass", "crackling fire"); keep the same words across
  consecutive shots in one place so the ambience flows. Empty for silence or pure music moments.
- referenceQuery: for a shot showing a real creature, object or place that must look accurate, its
  name for finding real reference photos, scientific name when it has one (e.g. "Melanocetus
  johnsonii", "Architeuthis dux", "Hubble Space Telescope"). Otherwise empty.
Keep a continuous visual flow between consecutive shots (scale, direction, colour).
{extra}Return only the JSON object."""
        user = [f"Title: {plan.title}. Aspect ratio {plan.aspectRatio}.\n", f"Concept: {concept.logline}\nBig idea: {concept.bigIdea}\n"]
        if not blank(concept.clockLabel):
            user.append(f"On-screen clock: {concept.clockLabel}\n")
        user.append("Script lines (index | spoken | seconds | text):\n")
        for line in lines:
            user.append(f"{line.index} | {'voice' if line.spoken else 'visual'} | {fixed(line.duration_sec, 1)} | {line.text}\n")
        user.append(notes(owner_notes))
        return self._llm.call(video_id, "SHOT_PLAN", system, "".join(user), ShotPlan, self._story_model)
