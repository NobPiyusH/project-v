"""The Inspector: a second, independent level of checking after every stage (owner, 16 Nov 2026). Port of Inspector.

A stage checks its own work; the Inspector checks the stored result again with different means: structural rules here (script length,
timing, missing pieces) and file checks on the media worker (Whisper, FFmpeg, image statistics). It never changes anything; the
workflow decides what a FAIL means (redo once with the findings as notes, then ask the owner).
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import replace
from typing import Any

from studio.agents.jfmt import fixed
from studio.media.types import Fact, Finding, InspectImage, InspectLine, MediaInspectRequest, MediaInspectResult
from studio.media.wire import to_json
from studio.planner.plan import VideoPlan
from studio.production import timing as nt
from studio.production.records import StoredScript
from studio.production.support import StageSupport, diagram_shot, stock_shot
from studio.workflow.stage import Stage
from studio.workflow.types import InspectionReport

INSPECTED = {Stage.RESEARCH, Stage.SCRIPT, Stage.MUSIC, Stage.NARRATION, Stage.SHOT_PLAN, Stage.KEYFRAMES, Stage.ANIMATIC, Stage.DRAFT_GEN,
             Stage.FINAL_GEN, Stage.ASSEMBLY}  # PLAN and CONCEPT have no hard rules yet
PER_SHOT = {Stage.KEYFRAMES, Stage.DRAFT_GEN, Stage.FINAL_GEN}  # stages whose findings name shots: a FAIL can be fixed by redoing only those
MAX_GAP_SEC = 2.5  # silence between two narration lines longer than this reads as dead air
_MEDIA = {Stage.NARRATION, Stage.MUSIC, Stage.KEYFRAMES, Stage.ANIMATIC, Stage.DRAFT_GEN, Stage.FINAL_GEN, Stage.ASSEMBLY}


def _fail(code: str, message: str, ref: str) -> Finding:
    return Finding("FAIL", code, message, ref)


def _warn(code: str, message: str, ref: str) -> Finding:
    return Finding("WARN", code, message, ref)


def _num(o: Any) -> float:
    return float(o) if isinstance(o, (int, float)) and not isinstance(o, bool) else 0.0


def shot_ref(s: dict[str, Any]) -> str:
    return f"shot {int(s['ord']) + 1}"


def word_count(text: str | None) -> int:
    return 0 if text is None or not text.strip() else len(re.split(r"\s+", text.strip()))


def normalise(text: str | None) -> str:
    return "" if text is None else re.sub(r"[\W_]+", " ", text.lower()).strip()


def mentions(normalised_text: str, item: str) -> bool:
    """True when every significant word (4+ letters) of item appears in the text. Light stemming: 'paradox' matches 'paradoxes',
    'expansion' matches 'expanding' via the first 6 letters."""
    words = normalise(item).split(" ")
    significant = [w for w in words if len(w) >= 4]
    check = significant or [w for w in words if w]
    return all((w[:6] if len(w) > 6 else w) in normalised_text for w in check)


# ------------------------------------------------------------------------------------------- pure checks


def research(plan: VideoPlan, facts: list[Fact]) -> list[Finding]:
    out: list[Finding] = []
    if plan.needsResearch and not facts:
        return [_fail("NO_FACTS", "Research found no facts for a factual video.", "research")]
    if plan.needsResearch and len(facts) < 3:
        out.append(_warn("FEW_FACTS", f"Only {len(facts)} facts: the script may lean on unchecked claims.", "research"))
    unsourced = sum(1 for f in facts if not f.sources)
    if unsourced > 0:
        out.append(_warn("UNSOURCED", f"{unsourced} fact{' has' if unsourced == 1 else 's have'} no source.", "research"))
    return out


def script(plan: VideoPlan, s: StoredScript | None) -> list[Finding]:
    out: list[Finding] = []
    if s is None or not s.lines:
        return [_fail("NO_SCRIPT", "The script has no lines.", "script")]
    spoken = [line for line in s.lines if line.spoken]
    for line in spoken:
        if line.text is None or not line.text.strip():
            out.append(_fail("EMPTY_LINE", "A spoken line has no words.", f"line {line.index + 1}"))
    words = sum(word_count(line.text) for line in spoken)
    is_short = plan.format == "short"
    # Same budget the Writer is given (2.1 words/s, 78% of the time spoken; sparse narration 75% of that).
    cap = plan.durationSec * 2.1 * 0.78 * (0.75 if plan.narration == "sparse" else 1.0)
    if plan.narration != "none" and words > cap * 1.15:
        out.append(_fail("SCRIPT_TOO_LONG", f"{words} words for a {plan.durationSec}s video (budget about {fixed(cap, 0)}): the voice won't fit.", "script"))
    if is_short and words / 2.6 > 58:
        out.append(_fail("SHORT_TOO_LONG", f"About {fixed(words / 2.6, 0)}s of speech: a Short must stay under 60s.", "script"))
    seen: set[str] = set()
    for line in spoken:
        key = normalise(line.text)
        if key and key in seen:
            out.append(_warn("REPEATED_LINE", "This line repeats an earlier one.", f"line {line.index + 1}"))
        seen.add(key)
    all_text = normalise(" ".join(line.text for line in spoken))
    for item in plan.mustInclude or []:
        if not mentions(all_text, item):
            out.append(_warn("MISSING_MUST_INCLUDE", f"The script doesn't mention “{item}”, which the prompt asked for.", "script"))
    return out


def narration(s: StoredScript | None) -> list[Finding]:
    if s is None:
        return [_fail("NO_SCRIPT", "There is no script to voice.", "script")]
    out: list[Finding] = []
    for line in s.lines:
        if not line.spoken:
            continue
        ref = f"line {line.index + 1}"
        if line.audio_uri is None:
            out.append(_fail("MISSING_VOICE", "This line has no voice recording.", ref))
        elif line.voice_status == "FAILED" or (line.check is not None and not line.check.startswith("coverage")
                                              and not line.check.startswith("only numbers")):
            out.append(_fail("VOICE_CHECK_FAILED", f"The voice stage's own check rejected every take ({line.check}).", ref))
    return out


def shot_plan(plan: VideoPlan, s: StoredScript | None, shots: list[dict[str, Any]]) -> list[Finding]:
    if not shots:
        return [_fail("NO_SHOTS", "The shot plan has no shots.", "shot plan")]
    out: list[Finding] = []
    tiny = sum(1 for sh in shots if _num(sh.get("duration_target_sec")) < 0.8)
    if tiny > 0:
        out.append(_warn("FLASH_SHOTS", f"{tiny} shot{' is' if tiny == 1 else 's are'} under 0.8s: too fast to read.", "shot plan"))
    if s is not None:
        out.extend(timing(plan, s))
    return out


def keyframes(shots: list[dict[str, Any]]) -> list[Finding]:
    return [Finding("FAIL", "MISSING_KEYFRAME", "This shot has no image.", shot_ref(sh), str(sh["id"]))
            for sh in shots if sh.get("keyframe_uri") is None and not diagram_shot(sh) and not stock_shot(sh)]


def missing_clips(shots: list[dict[str, Any]], drafts: bool) -> list[Finding]:
    """Every shot with an image needs a draft; every draft the owner didn't mark 'keep' needs a final."""
    out = []
    for sh in shots:
        need = (sh.get("keyframe_uri") is not None and sh.get("draft_uri") is None) if drafts else \
            (sh.get("draft_uri") is not None and sh.get("final_wanted") is True and sh.get("final_uri") is None)
        if not need or sh.get("clip_status") == "STOPPED" or sh.get("footage_asset_id") is not None:
            continue
        err = sh.get("clip_error")
        out.append(Finding("FAIL", "MISSING_CLIP", f"This shot has no {'draft' if drafts else 'final'} clip" + ("." if err is None else f": {err}"),
                           shot_ref(sh), str(sh["id"])))
    return out


def speech_end(s: StoredScript) -> float:
    """When the last word is spoken, using the same placement rules as the animatic."""
    tempo = 1.0 if s.tempo is None else s.tempo
    starts = s.line_starts or {}
    prev_end = 0.0
    for line in s.lines:
        if not line.spoken or line.duration_sec is None:
            continue
        want = starts.get(str(line.index))
        start = max(want, prev_end + 0.15) if want is not None else prev_end + 0.4
        prev_end = start + line.duration_sec / tempo
    return prev_end


def gaps(s: StoredScript) -> list[Finding]:
    out: list[Finding] = []
    if not s.line_starts:
        return out
    tempo = 1.0 if s.tempo is None else s.tempo
    prev = None
    prev_end = 0.0
    for line in s.lines:
        if not line.spoken or line.duration_sec is None:
            continue
        start = s.line_starts.get(str(line.index))
        if start is None:
            out.append(_warn("UNTIMED_LINE", "This line has no planned start: it may drift.", f"line {line.index + 1}"))
            continue
        if prev is not None and start - prev_end > MAX_GAP_SEC:
            out.append(_warn("DEAD_AIR", f"{fixed(start - prev_end, 1)}s of silence after this line (the voice is shorter than the shots "
                                         "planned for it). Redo the shot plan to retime.", f"line {prev.index + 1}"))
        prev = line
        prev_end = max(start, prev_end) + line.duration_sec / tempo
    return out


def timing(plan: VideoPlan, s: StoredScript) -> list[Finding]:
    """Short length and dead air, from the planned line starts and the real voice lengths."""
    out: list[Finding] = []
    end = speech_end(s)
    if plan.format == "short" and end + nt.TAIL > nt.SHORT_MAX + 0.05:
        out.append(_fail("SHORT_TOO_LONG", f"The voice ends at {fixed(end, 1)}s: with the closing beat the Short runs past 60s.", "shot plan"))
    out.extend(gaps(s))
    return out


def with_shot_targets(findings: list[Finding], shots: list[dict[str, Any]]) -> list[Finding]:
    """Links 'shot N' findings from the worker to their shot ids, so a FAIL can be fixed on that shot alone."""
    id_by_ref = {shot_ref(s): str(s["id"]) for s in shots}
    return [replace(f, target_id=id_by_ref[f.ref]) if f.target_id is None and f.ref is not None and f.ref in id_by_ref else f for f in findings]


def _describe(items: list[Finding], mx: int) -> str:
    parts = [(f"{f.ref}: " if f.ref is not None else "") + f.message for f in items[:mx]]
    return "; ".join(parts) + (f"; and {len(items) - mx} more." if len(items) > mx else "")


def report(findings: list[Finding], files_checked: int) -> InspectionReport:
    fails = [f for f in findings if f.severity == "FAIL"]
    warns = [f for f in findings if f.severity == "WARN"]
    verdict = "FAIL" if fails else "WARN" if warns else "PASS"
    checked = f" ({files_checked} files checked)" if files_checked > 0 else ""
    if verdict == "FAIL":
        summary = f"Inspector: FAIL, {len(fails)} problem{'' if len(fails) == 1 else 's'}{checked}: {_describe(fails, 3)}"
    elif verdict == "WARN":
        summary = f"Inspector: passed with {len(warns)} warning{'' if len(warns) == 1 else 's'}{checked}: {_describe(warns, 2)}"
    else:
        summary = "Inspector: all checks passed" + checked + "."
    notes = None if not fails else ("The Inspector rejected the previous attempt. Fix these problems:\n- "
                                    + "\n- ".join((f"{f.ref}: " if f.ref is not None else "") + f.message for f in fails))
    # A targeted redo (only the broken shots) is possible when every problem names its shot.
    targets = list(dict.fromkeys(f.target_id for f in fails)) if fails and all(f.target_id is not None for f in fails) else []
    return InspectionReport(verdict, summary, notes, targets)


# ------------------------------------------------------------------------------------------- the stage (database)


class Inspector(StageSupport):
    def _one_uri(self, sql: str, video_id: uuid.UUID) -> str | None:
        rows = self.db.rows(sql, {"v": video_id})
        return rows[0]["uri_hot"] if rows else None

    def clip_shots(self, video_id: uuid.UUID) -> list[dict[str, Any]]:
        return self.db.rows(
            """
            SELECT s.id, s.ord, s.final_wanted, s.clip_status, s.clip_error, s.footage_asset_id,
                   k.uri_hot AS keyframe_uri, d.uri_hot AS draft_uri, f.uri_hot AS final_uri
            FROM shot s JOIN scene sc ON sc.id = s.scene_id
            LEFT JOIN asset k ON k.id = s.keyframe_asset_id
            LEFT JOIN asset d ON d.id = s.draft_clip_asset_id
            LEFT JOIN asset f ON f.id = s.final_clip_asset_id
            WHERE sc.video_id = %(v)s ORDER BY s.ord
            """,
            {"v": video_id},
        )

    def _moon_is_a_layer(self, video_id: uuid.UUID) -> bool:
        from studio.planner import imported
        try:
            imp = imported.load(self.db, video_id)
        except Exception:  # noqa: BLE001 - a plan that cannot be read is judged like any other
            return False
        return imp is not None and imp.moon is not None and (imp.moon.get("mode") or "layer") == "layer"

    def media_request(self, video_id: uuid.UUID, stage: Stage) -> MediaInspectRequest | None:
        """What the media worker should look at, or None when this stage has no file checks."""
        if stage not in _MEDIA:
            return None
        if stage in (Stage.KEYFRAMES, Stage.DRAFT_GEN, Stage.FINAL_GEN) and self._moon_is_a_layer(video_id):
            # The plate is Moon-free ON PURPOSE (the Moon is composited by MOON_LAYER). The picture judge compared the plate with the narration
            # ("the colossal Moon is entirely missing"), failed it, and the automatic redo rented a second pod for the same result.
            return None
        plan = self.plan(video_id)
        is_short = plan.format == "short"
        s = self.latest_script(video_id)
        lines: list[InspectLine] = []
        images: list[InspectImage] = []
        music_uri = video_uri = None
        end = None
        if stage == Stage.NARRATION:
            lines = [InspectLine(line.index, line.text, line.audio_uri) for line in (s.lines if s else []) if line.spoken and line.audio_uri is not None]
        elif stage == Stage.MUSIC:
            music_uri = self._one_uri("SELECT a.uri_hot FROM music_cue m JOIN asset a ON a.id = m.asset_id WHERE m.video_id = %(v)s", video_id)
        elif stage == Stage.KEYFRAMES:
            images = [InspectImage(shot_ref(sh), sh["keyframe_uri"]) for sh in self.shots(video_id) if sh.get("keyframe_uri") is not None]
        elif stage == Stage.ANIMATIC:
            video_uri = self._one_uri("SELECT a.uri_hot FROM video v JOIN asset a ON a.id = v.animatic_asset_id WHERE v.id = %(v)s", video_id)
            end = None if s is None else speech_end(s)
        elif stage == Stage.ASSEMBLY:
            video_uri = self._one_uri("SELECT a.uri_hot FROM video v JOIN asset a ON a.id = v.master_asset_id WHERE v.id = %(v)s", video_id)
            end = None if s is None else speech_end(s)
        elif stage in (Stage.DRAFT_GEN, Stage.FINAL_GEN):
            key = "draft_uri" if stage == Stage.DRAFT_GEN else "final_uri"  # the worker's clip checks read the clips from "images"
            images = [InspectImage(shot_ref(sh), sh[key]) for sh in self.clip_shots(video_id) if sh.get(key) is not None]
        return MediaInspectRequest(str(video_id), stage.name, plan.aspectRatio, is_short, float(plan.durationSec), lines, music_uri, images,
                                   video_uri, end)

    def structural(self, video_id: uuid.UUID, stage: Stage) -> list[Finding]:
        plan, s = self.plan(video_id), self.latest_script(video_id)
        if stage == Stage.RESEARCH:
            return research(plan, self.facts(video_id))
        if stage == Stage.SCRIPT:
            return script(plan, s)
        if stage == Stage.NARRATION:
            return narration(s)
        if stage == Stage.MUSIC:
            if plan.music == "none":
                return []
            has = self.db.rows("SELECT count(*) AS n FROM music_cue WHERE video_id = %(v)s", {"v": video_id})[0]["n"] > 0
            return [] if has else [_fail("NO_MUSIC", "The plan wants music but none was stored.", "music")]
        if stage == Stage.SHOT_PLAN:
            return shot_plan(plan, s, self.shots(video_id))
        if stage == Stage.KEYFRAMES:
            return keyframes(self.shots(video_id))
        if stage == Stage.ANIMATIC:
            out = []
            if not self.db.rows("SELECT animatic_asset_id IS NOT NULL AS has FROM video WHERE id = %(v)s", {"v": video_id})[0]["has"]:
                out.append(_fail("NO_ANIMATIC", "No animatic was stored.", "video"))
            if s is not None:
                out.extend(gaps(s))
            return out
        if stage == Stage.ASSEMBLY:
            has = self.db.rows("SELECT master_asset_id IS NOT NULL AS has FROM video WHERE id = %(v)s", {"v": video_id})[0]["has"]
            return [] if has else [_fail("NO_MASTER", "No master was stored.", "video")]
        if stage == Stage.DRAFT_GEN:
            return missing_clips(self.clip_shots(video_id), True)
        if stage == Stage.FINAL_GEN:
            return missing_clips(self.clip_shots(video_id), False)
        return []

    def evaluate(self, video_id: uuid.UUID, stage: Stage, attempt: int, media: MediaInspectResult | None) -> InspectionReport:
        """Runs the structural checks, merges the worker's file findings, stores the report and returns the verdict."""
        findings = list(self.structural(video_id, stage))
        checked: list[str] = []
        if media is not None:
            findings.extend(media.findings or [])
            checked.extend(media.checked or [])
        if stage in PER_SHOT:
            findings = with_shot_targets(findings, self.shots(video_id))
        rep = report(findings, len(checked))
        self.db.execute(
            """
            INSERT INTO inspection (video_id, stage, attempt, verdict, findings_json, checked_json)
            VALUES (%(v)s, %(s)s, %(a)s, %(verdict)s, CAST(%(f)s AS jsonb), CAST(%(c)s AS jsonb))
            ON CONFLICT (video_id, stage, attempt) DO UPDATE
            SET verdict = EXCLUDED.verdict, findings_json = EXCLUDED.findings_json, checked_json = EXCLUDED.checked_json, created_at = now()
            """,
            {"v": video_id, "s": stage.name, "a": attempt, "verdict": rep.verdict, "f": json.dumps(to_json(findings)), "c": json.dumps(checked)},
        )
        return rep

    def reports(self, video_id: uuid.UUID) -> list[dict[str, Any]]:
        """Every stored report for a video, newest attempt first per stage (for the dashboard)."""
        rows = self.db.rows("SELECT stage, attempt, verdict, findings_json::text AS findings, checked_json::text AS checked, created_at "
                            "FROM inspection WHERE video_id = %(v)s ORDER BY stage, attempt DESC", {"v": video_id})
        return [{**r, "findings": json.loads(r["findings"]), "checked": json.loads(r["checked"])} for r in rows]
