"""Daily autopilot (owner approved 19 Nov 2026). Port of AutopilotService. Off until the owner turns it on. Each day at the start time it
takes the next idea (the owner's list first, then an unused Scout angle), starts one Dream Short with the AI Producer deciding inside the
Rs limits, and when the Short is finished writes a title, description and hashtags and marks it READY. Nothing is uploaded until the owner
taps Post. If a run fails or needs the owner, no new run starts until the owner deals with it.

tick() is called every minute by whoever owns the schedule (S7 wiring: the Java AutopilotScheduler's job); nothing here starts by itself."""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from studio.db import Db
from studio.dream import looks
from studio.dream.dream_studio import DreamStudio, NewDream
from studio.workflow.stage import StageMode

log = logging.getLogger("studio.autopilot")

BLOCKING = {"WAITING_START", "RUNNING", "NEEDS_YOU", "FAILED"}  # runs still in hand: a new day's run waits until none is left
STEPS = ["SCOUT", "WRITE", "PICTURES", "CLIPS", "MUSIC", "FINISH"]
PRIVACY = ("private", "unlisted", "public")
POST_WORKFLOW = "PostWorkflowPy"


class PostKit(BaseModel):
    """The publishing kit the writer makes for a finished Short."""

    title: str | None = None
    description: str | None = None
    hashtags: list[str] | None = None


@dataclass
class YoutubeUploadRequest:
    """One upload for the media worker, which has the file store and the owner's YouTube sign-in."""

    run_id: str
    video_uri: str
    title: str
    description: str
    tags: list[str]
    privacy: str


@dataclass
class YoutubeUploadResult:
    """video_id is None when it failed (error says why)."""

    video_id: str | None = None
    url: str | None = None
    privacy: str | None = None
    error: str | None = None


@dataclass
class Settings:
    enabled: bool
    start_time: str
    start_mode: str
    review_minutes: int
    daily_cap_inr: Decimal
    step_modes: dict[str, str] = field(default_factory=dict)
    look: str | None = None
    shots: int = 5
    use_scout: bool = True
    privacy: str = "private"


class Action(Enum):
    NOTHING = 1
    BLOCKED = 2
    CREATE = 3
    START_DUE = 4


def _utc(ts: Any) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def decide(enabled: bool, now: time, start_time: time, todays_run: dict[str, Any] | None, earlier_run_in_hand: bool, now_instant: datetime) -> Action:
    """What today's tick should do. Pure, so the rules are tested without a database or clock."""
    if not enabled:
        return Action.NOTHING
    if todays_run is not None:
        starts_at = todays_run.get("starts_at")
        due = todays_run.get("status") == "WAITING_START" and starts_at is not None and not _utc(starts_at) > now_instant
        return Action.START_DUE if due else Action.NOTHING
    if now < start_time:
        return Action.NOTHING
    return Action.BLOCKED if earlier_run_in_hand else Action.CREATE


def run_status(dream_status: str | None) -> str:
    """Status of a run from its Short's status. Pure."""
    s = dream_status or ""
    if s == "COMPLETED":
        return "READY"
    if s in ("WAITING", "WAITING_COST", "FAILED_WAITING"):
        return "NEEDS_YOU"
    if s == "CANCELLED":
        return "FAILED"
    return "RUNNING"


def clean(kit: PostKit | None) -> PostKit:
    """Keeps the kit inside YouTube's limits (title 100, description 5000, no angle brackets) with #Shorts first."""
    if kit is None or kit.title is None or not kit.title.strip():
        raise RuntimeError("the writer returned no title")
    title = re.sub(r"#\S+", "", re.sub(r"[<>]", "", kit.title)).strip()
    title = title[:100].strip() if len(title) > 100 else title
    desc = re.sub(r"[<>]", "", kit.description or "").strip()
    tags = ["#Shorts"]
    for h in kit.hashtags or []:
        t = "#" + re.sub(r"[^\w]", "", h)
        if len(t) > 1 and not any(x.lower() == t.lower() for x in tags) and len(tags) < 5:
            tags.append(t)
    desc = desc[:4500] if len(desc) > 4500 else desc
    return PostKit(title=title, description=desc, hashtags=tags)


class AutopilotService:
    def __init__(self, db: Db, studio: DreamStudio | None, llm: Any, zone: str = "Asia/Kolkata", clock: Callable[[], datetime] | None = None,
                 youtube_token_file: str = ".secrets/youtube-token.json"):
        self.db, self.studio, self.llm = db, studio, llm
        self.zone = ZoneInfo(zone)
        self.clock = clock or (lambda: datetime.now(UTC))
        self.youtube_token_file = youtube_token_file

    # ---- settings

    def settings(self) -> Settings:
        r = self.db.rows("SELECT * FROM autopilot_setting WHERE id = 1")[0]
        modes = r["step_modes"] if isinstance(r["step_modes"], dict) else json.loads(r["step_modes"])
        return Settings(r["enabled"], str(r["start_time"])[:5], r["start_mode"], int(r["review_minutes"]), Decimal(r["daily_cap_inr"]), dict(modes),
                        r["look"], int(r["shots"]), r["use_scout"], r["privacy"])

    def save_settings(self, s: Settings) -> Settings:
        mode = StageMode.parse(s.start_mode)  # validates
        if mode == StageMode.PRODUCER:
            raise ValueError("Start mode is Auto, Review or Manual")
        modes = {step: StageMode.parse((s.step_modes or {}).get(step) or "PRODUCER").name for step in STEPS}
        if s.privacy not in PRIVACY:
            raise ValueError("privacy must be private, unlisted or public")
        if s.daily_cap_inr is None or s.daily_cap_inr < 0:
            raise ValueError("daily cap must be ₹0 or more")
        self.db.execute(
            "UPDATE autopilot_setting SET enabled = %(e)s, start_time = CAST(%(t)s AS time), start_mode = %(sm)s, review_minutes = %(rm)s, "
            "daily_cap_inr = %(cap)s, step_modes = CAST(%(modes)s AS jsonb), look = %(look)s, shots = %(shots)s, use_scout = %(scout)s, "
            "privacy = %(p)s, updated_at = now() WHERE id = 1",
            {"e": s.enabled, "t": time.fromisoformat(s.start_time).isoformat(), "sm": mode.name, "rm": max(1, min(240, s.review_minutes)),
             "cap": s.daily_cap_inr, "modes": json.dumps(modes), "look": s.look if s.look and s.look.strip() else None,
             "shots": max(2, min(8, s.shots)), "scout": s.use_scout, "p": s.privacy})
        return self.settings()

    # ---- ideas

    def ideas(self) -> list[dict[str, Any]]:
        return self.db.rows("SELECT id, idea, look, source, used_at, created_at FROM autopilot_idea ORDER BY used_at NULLS FIRST, created_at")

    def add_idea(self, idea: str | None, look: str | None) -> uuid.UUID:
        if idea is None or not idea.strip():
            raise ValueError("Type an idea first")
        i = uuid.uuid4()
        self.db.execute("INSERT INTO autopilot_idea (id, idea, look, source) VALUES (%(id)s, %(i)s, %(l)s, 'owner')",
                        {"id": i, "i": idea.strip(), "l": look if look and look.strip() else None})
        return i

    def delete_idea(self, idea_id: uuid.UUID) -> None:
        self.db.execute("DELETE FROM autopilot_idea WHERE id = %(id)s", {"id": idea_id})

    def harvest_scout_angles(self) -> int:
        """Scout angles from earlier Shorts that were not the one used, added to the list once (source = scout)."""
        added = 0
        for r in self.db.rows("SELECT id, look, scout_json::text AS scout, scout_angle FROM dream_short WHERE scout_json IS NOT NULL "
                              "ORDER BY created_at DESC LIMIT 30"):
            brief = json.loads(r["scout"]).get("brief")
            angles = brief.get("angles") if isinstance(brief, dict) else None
            if not isinstance(angles, list):
                continue
            used = int(r["scout_angle"])
            for i, a in enumerate(angles):
                if i + 1 == used or not isinstance(a, dict) or a.get("idea") is None:
                    continue
                idea = str(a["idea"]).strip()
                before = self.db.rows("SELECT count(*) AS n FROM autopilot_idea")[0]["n"]
                self.db.execute("INSERT INTO autopilot_idea (id, idea, look, source) SELECT %(id)s, %(i)s, %(l)s, 'scout' "
                                "WHERE NOT EXISTS (SELECT 1 FROM autopilot_idea WHERE idea = %(i)s)",
                                {"id": uuid.uuid4(), "i": idea, "l": r["look"]})
                added += self.db.rows("SELECT count(*) AS n FROM autopilot_idea")[0]["n"] - before
        return added

    def next_idea(self, use_scout: bool) -> dict[str, Any] | None:
        """The next idea: the owner's own first (oldest), then Scout angles when allowed. None = none left."""
        sql = ("SELECT id, idea, look FROM autopilot_idea WHERE used_at IS NULL " + ("" if use_scout else "AND source = 'owner' ")
               + "ORDER BY CASE source WHEN 'owner' THEN 0 ELSE 1 END, created_at LIMIT 1")
        rows = self.db.rows(sql)
        if not rows and use_scout and self.harvest_scout_angles() > 0:
            rows = self.db.rows(sql)
        return rows[0] if rows else None

    # ---- the daily tick

    def tick(self) -> None:
        """Called every minute. Keeps runs in step with their Shorts, then maybe starts today's."""
        self.refresh_runs()
        s = self.settings()
        now = self.clock().astimezone(self.zone)
        today = now.date()
        run = self._run(today)
        in_hand = self.db.rows("SELECT count(*) AS n FROM autopilot_run WHERE run_date < %(d)s AND status = ANY(%(st)s)",
                               {"d": today, "st": sorted(BLOCKING)})[0]["n"] > 0
        action = decide(s.enabled, now.time().replace(tzinfo=None), time.fromisoformat(s.start_time), run, in_hand, now.astimezone(UTC))
        if action == Action.CREATE:
            self.create(today, s, now.astimezone(UTC))
        elif action == Action.START_DUE:
            self.start(run["id"], s)

    def _run(self, day: date) -> dict[str, Any] | None:
        rows = self.db.rows("SELECT * FROM autopilot_run WHERE run_date = %(d)s", {"d": day})
        return rows[0] if rows else None

    def create(self, today: date, s: Settings, now: datetime) -> None:
        """Today's run: picks the idea now, then starts (Auto), waits the review window (Review) or waits for Start (Manual)."""
        idea = self.next_idea(s.use_scout)
        run_id = uuid.uuid4()
        if idea is None:
            self.db.execute("INSERT INTO autopilot_run (id, run_date, status, message) VALUES (%(id)s, %(d)s, 'SKIPPED', %(m)s) ON CONFLICT DO NOTHING",
                            {"id": run_id, "d": today, "m": "No ideas left: add some to the list."})
            return
        mode = StageMode.parse(s.start_mode)
        starts_at = now if mode == StageMode.AUTO else None if mode == StageMode.MANUAL else now + timedelta(seconds=60 * s.review_minutes)
        before = self.db.rows("SELECT count(*) AS n FROM autopilot_run")[0]["n"]
        self.db.execute("INSERT INTO autopilot_run (id, run_date, status, message, idea, starts_at) "
                        "VALUES (%(id)s, %(d)s, 'WAITING_START', %(m)s, %(idea)s, %(at)s) ON CONFLICT (run_date) DO NOTHING",
                        {"id": run_id, "d": today, "m": "Waiting for you to tap Start." if mode == StageMode.MANUAL else "Starts at the set time.",
                         "idea": idea["idea"], "at": starts_at})
        if self.db.rows("SELECT count(*) AS n FROM autopilot_run")[0]["n"] == before:
            return
        self.db.execute("UPDATE autopilot_idea SET used_at = now() WHERE id = %(id)s", {"id": idea["id"]})
        self.db.execute("UPDATE autopilot_run SET message = %(m)s WHERE id = %(id)s",
                        {"id": run_id, "m": ("Waiting for you to tap Start." if mode == StageMode.MANUAL else "Starts soon.")
                         + ("" if idea.get("look") is None else f" Look: {idea['look']}")})
        if mode == StageMode.AUTO:
            self.start(run_id, s)

    def start(self, run_id: uuid.UUID, s: Settings | None = None) -> None:
        """The owner's Start (Manual), or the time came: start the Dream Short for this run."""
        s = s or self.settings()
        run = self.db.rows("SELECT * FROM autopilot_run WHERE id = %(id)s", {"id": run_id})[0]
        if run["status"] != "WAITING_START":
            return
        idea = run["idea"]
        rows = self.db.rows("SELECT look FROM autopilot_idea WHERE idea = %(i)s AND look IS NOT NULL LIMIT 1", {"i": idea})
        look = rows[0]["look"] if rows else s.look
        if look is None:  # rotate through the looks, one per day
            look = looks.LOOK_ORDER[run["run_date"].timetuple().tm_yday % len(looks.LOOK_ORDER)]
        try:
            dream = self.studio.create(NewDream(idea, look, s.shots, "480p", "PRODUCER", s.step_modes, 10, "bed", True, True, True, True, True))
            self.db.execute("UPDATE autopilot_run SET status = 'RUNNING', dream_short_id = %(d)s, message = %(m)s, starts_at = now(), "
                            "updated_at = now() WHERE id = %(id)s", {"d": dream, "m": f"Making the Short ({look}).", "id": run_id})
        except Exception as e:
            log.warning("Autopilot could not start today's Short: %s", e)
            self.db.execute("UPDATE autopilot_run SET status = 'FAILED', message = %(m)s, updated_at = now() WHERE id = %(id)s",
                            {"m": f"Could not start: {e}", "id": run_id})

    def start_now(self, run_id: uuid.UUID) -> None:
        self.start(run_id, self.settings())

    def refresh_runs(self) -> None:
        """Follows each running Short: needs you / failed / finished (then writes the publishing kit and marks READY)."""
        for r in self.db.rows("SELECT r.id, r.status, d.id AS dream_id, d.status AS dream_status, d.message AS dream_message FROM autopilot_run r "
                              "JOIN dream_short d ON d.id = r.dream_short_id WHERE r.status IN ('RUNNING', 'NEEDS_YOU')"):
            nxt = run_status(r["dream_status"])
            if nxt == "READY":
                try:
                    kit = self.post_kit(r["dream_id"])
                    self.db.execute("UPDATE autopilot_run SET status = 'READY', title = %(t)s, description = %(desc)s, hashtags = %(h)s, "
                                    "privacy = (SELECT privacy FROM autopilot_setting WHERE id = 1), "
                                    "message = 'Ready to post: check it and tap Post.', updated_at = now() WHERE id = %(id)s",
                                    {"t": kit.title, "desc": kit.description, "h": " ".join(kit.hashtags), "id": r["id"]})
                except Exception as e:
                    log.warning("Autopilot could not write the post kit: %s", e)
                    self.db.execute("UPDATE autopilot_run SET status = 'NEEDS_YOU', message = %(m)s, updated_at = now() WHERE id = %(id)s",
                                    {"m": f"Finished, but the title/description failed: {e} Write them yourself, or tap Retry.", "id": r["id"]})
            elif nxt != r["status"] or nxt == "NEEDS_YOU":
                self.db.execute("UPDATE autopilot_run SET status = %(s)s, message = %(m)s, updated_at = now() WHERE id = %(id)s",
                                {"s": nxt, "m": r["dream_message"] or "", "id": r["id"]})

    def post_kit(self, dream_id: uuid.UUID) -> PostKit:
        """Title (no hashtags), a short description and 3-5 hashtags incl. #Shorts, from the Short's own plan."""
        d = self.db.rows("SELECT idea, title, look FROM dream_short WHERE id = %(d)s", {"d": dream_id})[0]
        scenes = [r["scene"] for r in self.db.rows("SELECT scene FROM dream_shot WHERE dream_short_id = %(d)s ORDER BY ord", {"d": dream_id})]
        since = datetime.now(UTC)
        kit = self.llm.call(
            None, "autopilot.post",
            "You write the YouTube publishing kit for a Dreamscapes Short: a surreal, dreamlike vertical video with\n"
            "music only. Return JSON {\"title\", \"description\", \"hashtags\"}. title: max 70 characters, evocative,\n"
            "no hashtags, no clickbait, no emoji spam. description: 2-3 calm sentences that invite the viewer into\n"
            "the place, then one line \"Made with AI tools; music by the channel.\" hashtags: 3-5, each starting with\n"
            "#, the first is #Shorts. Never claim it is real footage.",
            f"Idea: {d['idea']}\nWorking title: {d.get('title') or ''}\nLook: {d['look']}\nShots:\n- " + "\n- ".join(scenes), PostKit)
        self.db.execute("UPDATE cost_entry SET dream_short_id = %(d)s WHERE dream_short_id IS NULL AND video_id IS NULL "
                        "AND note LIKE 'autopilot.post%%' AND ts >= %(since)s", {"d": dream_id, "since": since})
        return clean(kit)

    # ---- owner actions

    def runs(self) -> list[dict[str, Any]]:
        return self.db.rows("SELECT r.*, d.final_asset_id, d.status AS dream_status, d.step AS dream_step, COALESCE((SELECT sum(amount_inr) "
                            "FROM cost_entry c WHERE c.dream_short_id = r.dream_short_id), 0) AS spent_inr FROM autopilot_run r "
                            "LEFT JOIN dream_short d ON d.id = r.dream_short_id ORDER BY r.run_date DESC LIMIT 30")

    def status(self) -> dict[str, Any]:
        """What the screen shows at the top: on/off, next start, and why it is waiting (if it is)."""
        s = self.settings()
        now = self.clock().astimezone(self.zone)
        held = [r["h"] for r in self.db.rows("SELECT run_date::text || ': ' || status AS h FROM autopilot_run WHERE run_date < %(d)s "
                                             "AND status = ANY(%(st)s)", {"d": now.date(), "st": sorted(BLOCKING)})]
        return {"enabled": s.enabled, "zone": str(self.zone), "now": now.replace(tzinfo=None).isoformat(), "waitingOn": held,
                "ideasLeft": self.db.rows("SELECT count(*) AS n FROM autopilot_idea WHERE used_at IS NULL")[0]["n"],
                "youtubeConnected": self.youtube_connected()}

    def youtube_connected(self) -> bool:
        """The media worker keeps the owner's sign-in; the control plane only checks that the file exists (never reads it)."""
        p = Path(self.youtube_token_file)
        from studio.config import REPO_ROOT

        return p.exists() or (Path("..") / p).exists() or (REPO_ROOT / p).exists()

    def post(self, run_id: uuid.UUID, title: str | None, description: str | None, hashtags: str | None, privacy: str, gateway: Any = None) -> None:
        """The owner tapped Post: save their edits, then upload once. Refused unless the run is ready (no double posts)."""
        if privacy not in PRIVACY:
            raise ValueError("privacy must be private, unlisted or public")
        if title is None or not title.strip():
            raise ValueError("A title is needed")
        claimed = self.db.rows("UPDATE autopilot_run SET status = 'POSTING', title = %(t)s, description = %(desc)s, hashtags = %(h)s, privacy = %(p)s, "
                               "message = 'Uploading to YouTube…', updated_at = now() WHERE id = %(id)s AND status IN ('READY', 'POST_FAILED') "
                               "RETURNING id", {"t": title.strip(), "desc": (description or "").strip(), "h": (hashtags or "").strip(), "p": privacy,
                                                "id": run_id})
        if not claimed:
            raise RuntimeError("This Short is not ready to post (or is already posting)")
        if gateway is not None:
            gateway.start(POST_WORKFLOW, f"post-{run_id}-{int(self.clock().timestamp() * 1000)}", str(run_id))

    def dismiss(self, run_id: uuid.UUID) -> None:
        """A failed or stuck run the owner has dealt with: it stops holding up the next days."""
        self.db.execute("UPDATE autopilot_run SET status = 'SKIPPED', message = 'Dismissed by the owner.', updated_at = now() WHERE id = %(id)s "
                        "AND status IN ('WAITING_START', 'NEEDS_YOU', 'FAILED', 'RUNNING', 'READY', 'POST_FAILED')", {"id": run_id})

    def retry_kit(self, run_id: uuid.UUID) -> None:
        """Tries the publishing kit again for a finished Short whose kit failed."""
        self.db.execute("UPDATE autopilot_run SET status = 'RUNNING', updated_at = now() WHERE id = %(id)s AND status = 'NEEDS_YOU'", {"id": run_id})
        self.refresh_runs()

    # ---- posting (activities)

    def upload_request(self, run_id: uuid.UUID) -> YoutubeUploadRequest:
        r = self.db.rows("SELECT r.title, r.description, r.hashtags, r.privacy, a.uri_hot FROM autopilot_run r JOIN dream_short d "
                         "ON d.id = r.dream_short_id JOIN asset a ON a.id = d.final_asset_id WHERE r.id = %(id)s", {"id": run_id})[0]
        tags = [] if not (r["hashtags"] or "").strip() else r["hashtags"].strip().split()
        desc = (r["description"] or "") + ("" if not tags else "\n\n" + " ".join(tags))
        plain = [t.lower() for t in (re.sub(r"^#", "", x, count=1) for x in tags) if t.strip()]
        return YoutubeUploadRequest(str(run_id), r["uri_hot"], r["title"], desc, plain, r["privacy"])

    def save_upload(self, run_id: uuid.UUID, result: YoutubeUploadResult) -> str:
        if result.video_id is None:
            self.db.execute("UPDATE autopilot_run SET status = 'POST_FAILED', message = %(m)s, updated_at = now() WHERE id = %(id)s",
                            {"m": f"Upload failed: {result.error} Fix it and tap Post again.", "id": run_id})
            return "failed"
        with self.db.transaction():
            self.db.execute("UPDATE autopilot_run SET status = 'POSTED', youtube_id = %(y)s, privacy = %(p)s, posted_at = now(), message = %(m)s, "
                            "updated_at = now() WHERE id = %(id)s",
                            {"y": result.video_id, "p": result.privacy, "id": run_id, "m": f"Posted ({result.privacy}): {result.url}"})
            # Rule 6: the upload is a free API call (YouTube quota, ~1600 units); logged at Rs 0 against the Short.
            self.db.execute("INSERT INTO cost_entry (id, video_id, dream_short_id, provider_id, amount_inr, units, unit_type, estimated, note) "
                            "SELECT %(id)s, NULL, dream_short_id, 'youtube_api', 0, 1600, 'quota_unit', true, 'YouTube upload (videos.insert)' "
                            "FROM autopilot_run WHERE id = %(run)s", {"id": uuid.uuid4(), "run": run_id})
        return "posted"
