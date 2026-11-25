"""The M2a production stages: research, concept and script, music, narration, animatic. Ports of ResearchStage, StoryStage, MusicStage,
VoiceStage and AnimaticStage. Each stage builds the request for a media worker (and checks the budget first) and stores what comes back
(asset + provenance rows, cost entries, stage artefacts); no media passes through here, only URIs and small JSON."""

from __future__ import annotations

import json
import math
import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from studio.agents.chat import handles_claude
from studio.agents.jfmt import fixed, jround
from studio.agents.writer import VOICES
from studio.assets import asset_store as lic
from studio.assets.asset_store import NewAsset
from studio.ledger.budget_guard import GEMINI
from studio.media.types import (
    AnimaticLine,
    AnimaticRequest,
    AnimaticResult,
    AnimaticShot,
    MusicRequest,
    MusicResult,
    NarrationClip,
    NarrationLine,
    NarrationRequest,
    NarrationResult,
    ResearchRequest,
    ResearchResult,
    Usage,
)
from studio.media.wire import to_json
from studio.production import timing as nt
from studio.production.records import AudioVersion, StoredLine, StoredScript
from studio.production.support import StageSupport, round3, with_lines
from studio.provider.caps import check_provider_budget
from studio.workflow.types import StageResult


def _dumps(o: Any) -> str:
    return json.dumps(to_json(o), separators=(",", ":"))


def _words(text: str) -> int:
    return len(re.split(r"\s+", text))  # Java String.split("\\s+")


class ResearchStage(StageSupport):
    """Research: Gemini + Google Search request, and storing sourced facts."""

    def research_request(self, video_id: uuid.UUID, owner_notes: str | None) -> ResearchRequest:
        plan = self.plan(video_id)
        self.d.budget.require(Decimal(3), "RESEARCH", video_id)
        return ResearchRequest(self.prompt(video_id), plan.summary, plan.mustInclude, plan.language,
                               max(8, min(20, plan.durationSec // 15)), self.d.text_model, owner_notes)

    def save_research(self, video_id: uuid.UUID, r: ResearchResult) -> StageResult:
        with self.db.transaction():
            version = self.next_version("research", video_id)
            self.db.execute(
                "INSERT INTO research (id, video_id, version, facts_json, sources_json, queries, model) "
                "VALUES (%(id)s, %(v)s, %(ver)s, CAST(%(facts)s AS jsonb), CAST(%(sources)s AS jsonb), %(queries)s, %(model)s)",
                {"id": uuid.uuid4(), "v": video_id, "ver": version, "facts": _dumps(r.facts), "sources": _dumps(r.sources),
                 "queries": list(r.queries or []), "model": r.model})
            queries = len(r.queries or [])
            # The Claude backup researcher records its own cost (tokens + searches) as soon as it answers.
            if not handles_claude(r.model):
                self.d.ledger.record(video_id, GEMINI, self.d.pricing.text_inr(r.usage) + self.d.pricing.grounding_inr(queries),
                                     Decimal(0 if r.usage is None else r.usage.total_tokens), "tokens", True,
                                     f"RESEARCH ({r.model}, {queries} searches)")
        sourced = sum(1 for f in r.facts if f.sources)
        confirmed = sum(1 for f in r.facts if f.check == "confirmed")
        unconfirmed = sum(1 for f in r.facts if f.check == "unconfirmed")
        checked = "" if confirmed + unconfirmed == 0 else (
            f" Source check: {confirmed} confirmed on their page, {unconfirmed} unconfirmed (writer told to avoid those numbers).")
        summary = (f"{len(r.facts)} facts ({sourced} with sources) from {len(r.sources or [])} web sources"
                   f"{' (Claude researcher)' if handles_claude(r.model) else ''}." + checked)
        return StageResult.of(summary, json.dumps({"facts": to_json(r.facts), "queries": r.queries}, separators=(",", ":")))


class StoryStage(StageSupport):
    """Concept and script (Writer agent)."""

    def concept(self, video_id: uuid.UUID, owner_notes: str | None) -> StageResult:
        plan = self.plan(video_id)
        c = self.d.writer.concept(video_id, self.prompt(video_id), plan, self.facts(video_id), owner_notes)
        with self.db.transaction():
            version = self.next_version("concept", video_id)
            clock = {"label": c.clockLabel or "", "logline": c.logline or "", "emotionalArc": c.emotionalArc or ""}
            self.db.execute(
                "INSERT INTO concept (id, video_id, big_idea, clock_definition, acts_json, version) "
                "VALUES (%(id)s, %(v)s, %(idea)s, CAST(%(clock)s AS jsonb), CAST(%(acts)s AS jsonb), %(ver)s)",
                {"id": uuid.uuid4(), "v": video_id, "idea": c.bigIdea, "clock": json.dumps(clock, separators=(",", ":")),
                 "acts": json.dumps([a.model_dump() for a in c.acts or []], separators=(",", ":")), "ver": version})
        return StageResult.of(f"{c.logline} Big idea: {c.bigIdea}", json.dumps(c.model_dump(), separators=(",", ":")))

    def script(self, video_id: uuid.UUID, owner_notes: str | None) -> StageResult:
        plan = self.plan(video_id)
        concept = self.latest_concept(video_id)
        if concept is None:
            raise RuntimeError("No concept yet")
        s = self.d.writer.script(video_id, self.prompt(video_id), plan, concept, self.facts(video_id), owner_notes)
        if not s.lines:
            raise RuntimeError("The writer returned an empty script")
        lines = [StoredLine(i, ln.text, ln.act, ln.purpose, bool(ln.spoken) and plan.narration != "none", delivery=ln.delivery)
                 for i, ln in enumerate(s.lines)]
        voice = s.voiceName if s.voiceName in VOICES else self.d.default_voice
        with self.db.transaction():
            stored = StoredScript(self.next_version("script", video_id), lines, voice, s.voiceStyle, s.musicBrief, None, None)
            words = sum(_words(ln.text) for ln in lines if ln.spoken)
            md = "".join(("" if ln.spoken else "_[visual]_ ") + ln.text + "\n\n" for ln in lines)
            self.db.execute(
                "INSERT INTO script (id, video_id, version, narration_md, beat_sheet_json, word_count, est_duration_sec) "
                "VALUES (%(id)s, %(v)s, %(ver)s, %(md)s, CAST(%(beats)s AS jsonb), %(words)s, %(est)s)",
                {"id": uuid.uuid4(), "v": video_id, "ver": stored.version, "md": md, "beats": _dumps(stored), "words": words,
                 "est": jround(words / 2.3)})
        spoken = sum(1 for ln in lines if ln.spoken)
        return StageResult.of(f"{len(lines)} lines ({spoken} spoken, {words} words), voice {voice}.", _dumps(stored))


class MusicStage(StageSupport):
    """Score request (Lyria) and storing the beat-mapped music cue."""

    def music_request(self, video_id: uuid.UUID, owner_notes: str | None) -> MusicRequest:
        plan = self.plan(video_id)
        script = self.latest_script(video_id)
        target = float(plan.durationSec)
        max_segments = int(min(6, math.ceil(target / 55.0) + 1))
        self.d.budget.require(self.d.pricing.music_inr(max_segments), f"MUSIC ({max_segments} Lyria generations at most)", video_id)
        check_provider_budget("gemini_lyria", self.d.pricing.music_inr(max_segments), stage="MUSIC", scope_id=video_id)  # Lyria is not routed: its own cap
        brief = script.music_brief if script is not None and script.music_brief is not None else f"Cinematic score matching the tone: {plan.tone}"
        notes = "" if owner_notes is None or not owner_notes.strip() else "Owner's notes: " + owner_notes
        prompt = (f"Instrumental film score, no vocals, no lyrics. About {jround(target)} seconds long.\n"
                  f"Mood and style: {brief}\n"
                  f"Video: {plan.title} ({plan.tone}). It must leave room for a narrator's voice: avoid busy melodies in the mid range.\n"
                  f"{notes}")
        return MusicRequest(str(video_id), self.d.music_model, prompt, target, max_segments)

    def save_music(self, video_id: uuid.UUID, r: MusicResult) -> StageResult:
        songs = r.segments if r.usage is None else len(r.usage)
        with self.db.transaction():
            self.d.ledger.record(video_id, GEMINI, self.d.pricing.music_inr(songs), Decimal(songs), "song", True, f"MUSIC ({r.model})")
            asset_id = self.d.assets.create(NewAsset(video_id, "audio", r.uri, "audio/wav", None, None, r.duration_sec, "gemini_api", r.model,
                                                     None, None, lic.LICENCE_GEMINI, True))
            self.db.execute("DELETE FROM music_cue WHERE video_id = %(v)s", {"v": video_id})
            self.db.execute(
                "INSERT INTO music_cue (id, video_id, asset_id, bpm, beat_times_json, sections_json) "
                "VALUES (%(id)s, %(v)s, %(a)s, %(bpm)s, CAST(%(beats)s AS jsonb), CAST(%(sections)s AS jsonb))",
                {"id": uuid.uuid4(), "v": video_id, "a": asset_id, "bpm": r.bpm, "beats": json.dumps(r.beat_times),
                 "sections": json.dumps({"downbeats": r.downbeat_times})})
        return StageResult.of(
            f"Score {fixed(r.duration_sec, 0)}s at {fixed(r.bpm, 0)} BPM, {len(r.beat_times)} beats ({songs} Lyria generation{'' if songs == 1 else 's'}).",
            json.dumps({"assetId": str(asset_id), "bpm": r.bpm, "durationSec": r.duration_sec}, separators=(",", ":")))


class VoiceStage(StageSupport):
    """Narration: whole-script voicing, per-line re-voicing, direct line editing, take selection."""

    def narration_request(self, video_id: uuid.UUID) -> NarrationRequest:
        plan = self.plan(video_id)
        s = self.latest_script(video_id)
        if s is None:
            raise RuntimeError("No script yet")
        lines = [NarrationLine(ln.index, ln.text, ln.delivery) for ln in s.lines if ln.spoken]
        # ~25 audio tokens per second of speech at ~2.3 words per second.
        words = sum(_words(ln.text) for ln in lines)
        self.d.budget.require(self.d.pricing.tts_inr(Usage(words * 2, jround(words / 2.3 * 25 * 1.5), 0, 0)), "NARRATION", video_id)
        # Short "in a <style> voice" phrase: long instructions get read aloud by the TTS model (owner feedback).
        raw = re.sub(r"[.\s]+$", "", (s.voice_style or "").strip())
        short_style = " ".join(re.split(r"\s+", raw)[:8]).lower()
        style = "in a calm documentary narrator's voice" if not short_style.strip() else f"in a {short_style} voice"
        return NarrationRequest(str(video_id), self.d.tts_model, self.d.text_model, s.voice_name, style, plan.language, lines)

    def save_narration(self, video_id: uuid.UUID, r: NarrationResult) -> StageResult:
        with self.db.transaction():
            self._apply_clips(video_id, r, None, "NARRATION")
        retakes = sum(max(0, c.takes - 1) for c in r.lines)
        failed = [f"#{c.index} ({c.check})" for c in r.lines if not c.passed]
        seconds = sum(c.duration_sec for c in r.lines)
        summary = (f"{len(r.lines)} lines voiced by {r.voice_name}, {fixed(seconds, 0)}s of speech. Every line checked by transcription: "
                   f"{retakes} re-voiced automatically"
                   f"{', all pass' if not failed else '; still imperfect after 3 takes: ' + ', '.join(failed)}.")
        return StageResult.of(summary, json.dumps({"lines": len(r.lines), "retakes": retakes, "failed": failed}, separators=(",", ":")))

    def _apply_clips(self, video_id: uuid.UUID, r: NarrationResult, style: str | None, purpose: str) -> None:
        """Stores new voice takes (whole stage or single lines): asset + provenance per take, version kept, cost recorded."""
        s = self.lock_script(video_id)
        by_index: dict[int, NarrationClip] = {}
        p = o = t = tot = 0
        for c in r.lines:
            by_index[c.index] = c
            if c.usage is not None:
                p, o, t, tot = p + c.usage.prompt_tokens, o + c.usage.output_tokens, t + c.usage.thought_tokens, tot + c.usage.total_tokens
        total = Usage(p, o, t, tot)
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        if r.eleven_labs():
            provider, licence = "elevenlabs", lic.LICENCE_ELEVENLABS_FREE
        elif r.chatterbox():
            provider, licence = "chatterbox", lic.LICENCE_CHATTERBOX
        elif r.kokoro():
            provider, licence = "kokoro", lic.LICENCE_KOKORO
        else:
            provider, licence = "gemini_api", lic.LICENCE_GEMINI
        lines: list[StoredLine] = []
        for ln in s.lines:
            c = by_index.get(ln.index)
            if c is None:
                lines.append(ln)
                continue
            asset_id = self.d.assets.create(NewAsset(video_id, "audio", c.uri, "audio/wav", None, None, c.duration_sec, provider, r.model,
                                                     f"[{r.voice_name}] {ln.text}", None, licence, True))
            lines.append(ln.with_new_take(AudioVersion(asset_id, c.uri, c.duration_sec, now, c.check, style, ln.text), c.passed))
        self.write_script(video_id, with_lines(s, lines))
        if r.eleven_labs():
            # Free plan: no money, but the credits are tracked (10,000 a month).
            self.d.ledger.record(video_id, "elevenlabs", self.d.pricing.eleven_labs_inr(r.characters), Decimal(r.characters), "credits", False,
                                 f"{purpose} ({r.model}, {len(r.lines)} lines)")
        elif not r.kokoro() and not r.chatterbox():  # local voices run free on the laptop: nothing to record
            self.d.ledger.record(video_id, GEMINI, self.d.pricing.tts_inr(total), Decimal(total.total_tokens), "tokens", True,
                                 f"{purpose} ({r.model}, {len(r.lines)} lines)")

    def edit_line(self, video_id: uuid.UUID, index: int, text: str | None, spoken: bool | None) -> StoredLine:
        """Owner edits a line. The writer's original is kept; a spoken line whose text changed is flagged "needs voice" (its old audio no
        longer matches) until it is re-voiced. Recorded as an edit for taste learning."""
        clean = re.sub(r"\s+", " ", (text or "").strip())
        if not clean:
            raise RuntimeError("A line can't be empty")
        with self.db.transaction():
            s = self.lock_script(video_id)
            lines: list[StoredLine] = []
            updated: StoredLine | None = None
            for ln in s.lines:
                if ln.index == index:
                    updated = ln.with_edit(clean, ln.spoken if spoken is None else spoken)
                    if clean != ln.text:
                        self.db.execute(
                            "INSERT INTO preference_event (id, kind, video_id, context_json, chosen, rejected) "
                            "VALUES (%(id)s, 'edit_diff', %(v)s, CAST(%(ctx)s AS jsonb), %(chosen)s, %(rejected)s)",
                            {"id": uuid.uuid4(), "v": video_id, "ctx": json.dumps({"stage": "SCRIPT", "line": index}, separators=(",", ":")),
                             "chosen": clean, "rejected": ln.text})
                    lines.append(updated)
                else:
                    lines.append(ln)
            if updated is None:
                raise LookupError(f"No line {index}")
            self.write_script(video_id, with_lines(s, lines))
        return updated

    def line_narration_request(self, video_id: uuid.UUID, indexes: list[int], style_notes: str | None) -> NarrationRequest:
        """Re-voice only the given lines. style_notes (e.g. "slower and more hushed") replaces the reading style."""
        base = self.narration_request(video_id)
        wanted = set(indexes)
        lines = [ln for ln in base.lines if ln.index in wanted]
        if not lines:
            raise RuntimeError("None of those lines are spoken lines of this video")
        style = base.style
        if style_notes is not None and style_notes.strip():
            w = re.split(r"\s+", re.sub(r"[.\s]+$", "", style_notes.strip()))
            # Kept short on purpose: long style text gets read aloud by the TTS model.
            style = "in a voice that is " + " ".join(w[:12]).lower()
        # A redo keeps the voice the video already has (never a second voice in one video).
        return NarrationRequest(base.video_id, base.model, base.check_model, base.voice_name, style, base.language, lines,
                                self.current_provider(video_id))

    def current_provider(self, video_id: uuid.UUID) -> str:
        """"elevenlabs", "gemini", "chatterbox" or "kokoro": who voiced this video's narration most recently."""
        rows = self.db.rows(
            "SELECT p.provider FROM asset a JOIN provenance p ON p.id = a.provenance_id "
            "WHERE a.video_id = %(v)s AND a.kind = 'audio' AND a.uri_hot LIKE '%%/narration/%%' "
            "AND p.provider IN ('elevenlabs', 'gemini_api', 'kokoro', 'chatterbox') ORDER BY a.created_at DESC LIMIT 1", {"v": video_id})
        if not rows:
            return "auto"
        return "gemini" if rows[0]["provider"] == "gemini_api" else rows[0]["provider"]

    def save_line_narration(self, video_id: uuid.UUID, r: NarrationResult, style: str | None) -> StageResult:
        with self.db.transaction():
            self._apply_clips(video_id, r, style, "VOICE REDO")
        imperfect = sum(1 for c in r.lines if not c.passed)
        return StageResult.of(f"{len(r.lines)} line(s) re-voiced"
                              f"{', all pass the transcript check' if imperfect == 0 else f'; {imperfect} still imperfect'}.", None)

    def mark_lines(self, video_id: uuid.UUID, indexes: list[int], status: str | None, why: str | None) -> None:
        with self.db.transaction():
            s = self.lock_script(video_id)
            wanted = set(indexes)
            self.write_script(video_id, with_lines(s, [ln.with_status(status, why) if ln.index in wanted else ln for ln in s.lines]))

    def select_line_voice(self, video_id: uuid.UUID, index: int, asset_id: uuid.UUID) -> None:
        """Switch a line back (or forward) to any earlier voice take. Free."""
        with self.db.transaction():
            s = self.lock_script(video_id)
            found = False
            lines: list[StoredLine] = []
            for ln in s.lines:
                if ln.index == index:
                    v = next((x for x in ln.versions() if x.asset_id == asset_id), None)
                    if v is None:
                        raise RuntimeError(f"That take is not a version of line {index}")
                    lines.append(ln.with_active(v))
                    found = True
                else:
                    lines.append(ln)
            if not found:
                raise LookupError(f"No line {index}")
            self.write_script(video_id, with_lines(s, lines))


class AnimaticStage(StageSupport):
    """Animatic request (keyframes + voice + music on the planned timeline) and result."""

    def animatic_request(self, video_id: uuid.UUID) -> AnimaticRequest:
        plan = self.plan(video_id)
        shot_rows = self.shots(video_id)
        shots: list[AnimaticShot] = []
        line_start: dict[int, float] = {}
        last_image: str | None = None
        end = 0.0
        for i, s in enumerate(shot_rows):
            p = json.loads(s["prompt_json"])
            start = float(p["startSec"])
            dur = float(s["duration_target_sec"])
            image = last_image if s["keyframe_uri"] is None else s["keyframe_uri"]
            if image is None:
                continue  # no keyframe at all yet for the opening shots
            last_image = image
            shots.append(AnimaticShot(i + 1, start, dur, image, s["overlay_text"]))
            li = p.get("lineIndex")
            if isinstance(li, (int, float)) and not isinstance(li, bool):
                line_start.setdefault(int(li), start)
            end = max(end, start + dur)
        if not shots:
            raise RuntimeError("No keyframes to build an animatic from")
        narration: list[AnimaticLine] = []
        script = self.latest_script(video_id)
        tempo = 1.0 if script is None or script.tempo is None else script.tempo
        prev_end = 0.0
        for p in nt.place(script, line_start):
            narration.append(AnimaticLine(p.line.audio_uri, round3(p.start_sec), tempo))
            prev_end = p.end_sec
        # Never cut words: if the voice runs past the pictures, hold the last shot longer.
        needed = prev_end + nt.TAIL
        if needed > end and shots:
            last = shots.pop()
            shots.append(AnimaticShot(last.index, last.start_sec, last.duration_sec + (needed - end), last.image_uri, last.overlay_text))
            end = needed
        rows = self.db.rows("SELECT a.uri_hot FROM music_cue m JOIN asset a ON a.id = m.asset_id WHERE m.video_id = %(v)s", {"v": video_id})
        music_uri = rows[0]["uri_hot"] if rows else None
        return AnimaticRequest(str(video_id), plan.title, shots, narration, music_uri, max(end, prev_end), plan.aspectRatio)

    def save_animatic(self, video_id: uuid.UUID, r: AnimaticResult) -> StageResult:
        vertical = self.plan(video_id).aspectRatio == "9:16"
        with self.db.transaction():
            asset_id = self.d.assets.create(NewAsset(video_id, "video", r.uri, "video/mp4", 1080 if vertical else 1920,
                                                     1920 if vertical else 1080, r.duration_sec, "projectv", "ffmpeg animatic", None, None,
                                                     lic.LICENCE_OWN_RENDER, False))
            self.db.execute("UPDATE video SET animatic_asset_id = %(a)s, updated_at = now() WHERE id = %(v)s", {"a": asset_id, "v": video_id})
        return StageResult.of(
            f"Animatic ready: {fixed(r.duration_sec, 0)}s, 1080p, keyframes + narration + music. Story and pacing check before any video spend.",
            json.dumps({"assetId": str(asset_id), "durationSec": r.duration_sec}, separators=(",", ":")))
