"""The autopilot: ports of AutopilotRulesTest, AutopilotPersistenceTest and AutopilotDailyCapTest, plus the post workflow on Temporal's
time-skipping test server. No Temporal client, no LLM, no network: only paths that do not start a Short or write a kit hit the database."""

import asyncio
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from studio.autopilot import service as ap
from studio.autopilot.service import Action, AutopilotService, PostKit, YoutubeUploadResult, clean, decide, run_status
from studio.tests.dworld import make_world

SEVEN = time(7, 0)
NOW = datetime(2026, 11, 20, 2, 0, tzinfo=UTC)
IST = ZoneInfo("Asia/Kolkata")


def test_off_by_default_does_nothing():
    assert decide(False, time(9, 0), SEVEN, None, False, NOW) == Action.NOTHING


def test_starts_once_the_time_has_come():
    assert decide(True, time(6, 59), SEVEN, None, False, NOW) == Action.NOTHING
    assert decide(True, time(7, 0), SEVEN, None, False, NOW) == Action.CREATE
    assert decide(True, time(15, 30), SEVEN, None, False, NOW) == Action.CREATE  # the laptop was off at 7: it catches up


def test_one_run_per_day():
    assert decide(True, time(9, 0), SEVEN, {"status": "RUNNING"}, False, NOW) == Action.NOTHING


def test_a_failed_or_waiting_earlier_run_holds_the_next_day():
    assert decide(True, time(9, 0), SEVEN, None, True, NOW) == Action.BLOCKED
    assert {"FAILED", "NEEDS_YOU", "RUNNING", "WAITING_START"} <= ap.BLOCKING
    assert "READY" not in ap.BLOCKING, "a Short waiting for Post does not stop the next one"


def test_review_mode_starts_when_its_window_ends():
    waiting = {"status": "WAITING_START", "starts_at": NOW + timedelta(seconds=60)}
    assert decide(True, time(9, 0), SEVEN, waiting, False, NOW) == Action.NOTHING
    assert decide(True, time(9, 1), SEVEN, waiting, False, NOW + timedelta(seconds=60)) == Action.START_DUE


def test_manual_mode_never_starts_by_itself():
    assert decide(True, time(23, 0), SEVEN, {"status": "WAITING_START", "starts_at": None}, False, NOW) == Action.NOTHING


def test_a_run_follows_its_short():
    assert run_status("COMPLETED") == "READY" and run_status("FAILED_WAITING") == "NEEDS_YOU" and run_status("WAITING_COST") == "NEEDS_YOU"
    assert run_status("CANCELLED") == "FAILED" and run_status("WAITING_REVIEW") == "RUNNING" and run_status(None) == "RUNNING"


def test_the_post_kit_is_cleaned_for_youtube():
    k = clean(PostKit(title="The <quiet> station #dream " + "x" * 120, description="Calm <b>place</b>.",
                      hashtags=["#Dreamcore", "liminal spaces", "#shorts", "#a", "#b", "#c"]))
    assert len(k.title) <= 100 and "<" not in k.title and "#" not in k.title
    assert k.hashtags[0] == "#Shorts" and len(k.hashtags) == 5 and "#liminalspaces" in k.hashtags
    assert sum(1 for h in k.hashtags if h.lower() == "#shorts") == 1
    with pytest.raises(RuntimeError):
        clean(PostKit(title=" ", description="", hashtags=[]))


# ------------------------------------------------------------------------------------------------ records on a scratch Postgres


@pytest.fixture
def db(scratch_db):
    for t in ("autopilot_run", "autopilot_idea"):
        scratch_db.execute(f"DELETE FROM {t}")
    scratch_db.execute("UPDATE autopilot_setting SET enabled = false, start_mode = 'AUTO', start_time = '07:00', daily_cap_inr = 50, "
                       "use_scout = true, privacy = 'private'")
    return scratch_db


def at(db, ist_time, studio=None, llm=None):
    t = datetime.combine(date(2026, 11, 20), time.fromisoformat(ist_time), tzinfo=IST)
    return AutopilotService(db, studio, llm, "Asia/Kolkata", lambda: t.astimezone(UTC))


def runs(db):
    return db.rows("SELECT count(*) AS n FROM autopilot_run")[0]["n"]


def test_off_by_default_and_every_step_defaults_to_the_producer(db):
    s = at(db, "08:00").settings()
    assert not s.enabled and s.step_modes["CLIPS"] == "PRODUCER" and s.privacy == "private"
    assert s.daily_cap_inr == Decimal(50), "owner set the daily cap to Rs 50"
    at(db, "08:00").tick()
    assert runs(db) == 0


def test_owner_ideas_come_first_then_unused_scout_angles(db):
    a = at(db, "08:00")
    dream = uuid.uuid4()
    db.execute("INSERT INTO dream_short (id, idea, status, look, scout_angle, scout_json) VALUES (%(id)s, 'x', 'COMPLETED', 'drive', 1, "
               "CAST(%(j)s AS jsonb))", {"id": dream, "j": '{"brief": {"angles": [{"title": "a", "idea": "used angle"}, '
                                                          '{"title": "b", "idea": "a tram under the sea"}]}}'})
    a.add_idea("my own idea", None)
    assert a.next_idea(True)["idea"] == "my own idea"
    db.execute("UPDATE autopilot_idea SET used_at = now()")
    assert a.next_idea(False) is None, "Scout ideas only when allowed"
    scout = a.next_idea(True)
    assert scout["idea"] == "a tram under the sea" and scout["look"] == "drive"
    assert a.harvest_scout_angles() == 0, "each angle is added once"


def test_manual_start_waits_and_only_one_run_per_day(db):
    db.execute("UPDATE autopilot_setting SET enabled = true, start_mode = 'MANUAL'")
    at(db, "06:00").tick()
    assert runs(db) == 0, "before the start time"
    at(db, "08:00").add_idea("glass orchard", None)
    at(db, "08:00").tick()
    at(db, "09:00").tick()
    assert runs(db) == 1
    assert db.rows("SELECT status FROM autopilot_run")[0]["status"] == "WAITING_START"


def test_no_ideas_means_a_skipped_day_not_an_error(db):
    db.execute("UPDATE autopilot_setting SET enabled = true, use_scout = false")
    at(db, "08:00").tick()
    assert db.rows("SELECT status FROM autopilot_run")[0]["status"] == "SKIPPED"


def test_a_failed_run_holds_the_next_day_until_dismissed(db):
    db.execute("UPDATE autopilot_setting SET enabled = true, start_mode = 'MANUAL'")
    failed = uuid.uuid4()
    db.execute("INSERT INTO autopilot_run (id, run_date, status) VALUES (%(id)s, DATE '2026-11-19', 'FAILED')", {"id": failed})
    at(db, "08:00").add_idea("glass orchard", None)
    at(db, "08:00").tick()
    assert runs(db) == 1, "held"
    assert len(at(db, "08:00").status()["waitingOn"]) == 1
    at(db, "08:00").dismiss(failed)
    at(db, "08:00").tick()
    assert runs(db) == 2


def test_auto_mode_starts_the_short_with_the_producer_deciding(db):
    w = make_world(db)
    db.execute("UPDATE autopilot_setting SET enabled = true, start_mode = 'AUTO', look = 'drive', shots = 3")
    a = at(db, "08:00", w.studio)
    a.add_idea("glass orchard", None)
    a.tick()
    run = db.rows("SELECT * FROM autopilot_run")[0]
    assert run["status"] == "RUNNING" and run["dream_short_id"] is not None and "Making the Short (drive)" in run["message"]
    d = w.dreams.dream(run["dream_short_id"])
    assert d["idea"] == "glass orchard" and d["look"] == "drive" and d["shot_count"] == 3 and d["producer_decides"] is True
    assert d["scout"] is True and set(d["modes_json"]) == {"SCOUT", "WRITE", "PICTURES", "CLIPS", "MUSIC", "FINISH"}
    assert d["modes_json"]["CLIPS"] == "PRODUCER" and d["modes_json"]["WRITE"] == "PRODUCER"
    assert w.gateway.started[0][2]["defaultMode"] == "PRODUCER"
    assert db.rows("SELECT used_at FROM autopilot_idea")[0]["used_at"] is not None


def test_a_finished_short_gets_its_post_kit_and_becomes_ready(db):
    class Llm:
        def call(self, video_id, purpose, system, user, tp, preferred=None):
            assert purpose == "autopilot.post" and "Idea: a pier" in user
            return PostKit(title="The pier #dream", description="Calm.", hashtags=["dream", "pier"])

    dream = uuid.uuid4()
    db.execute("INSERT INTO dream_short (id, idea, status, title) VALUES (%(id)s, 'a pier', 'COMPLETED', 'Pier')", {"id": dream})
    run = uuid.uuid4()
    db.execute("INSERT INTO autopilot_run (id, run_date, status, dream_short_id) VALUES (%(id)s, DATE '2026-11-18', 'RUNNING', %(d)s)",
               {"id": run, "d": dream})
    at(db, "08:00", llm=Llm()).refresh_runs()
    r = db.rows("SELECT * FROM autopilot_run WHERE id = %(id)s", {"id": run})[0]
    assert r["status"] == "READY" and r["title"] == "The pier" and r["hashtags"] == "#Shorts #dream #pier" and r["privacy"] == "private"


def test_post_is_refused_unless_ready_and_an_upload_is_recorded(db):
    dream = uuid.uuid4()
    db.execute("INSERT INTO dream_short (id, idea, status) VALUES (%(id)s, 'x', 'COMPLETED')", {"id": dream})
    run = uuid.uuid4()
    db.execute("INSERT INTO autopilot_run (id, run_date, status, dream_short_id) VALUES (%(id)s, DATE '2026-11-18', 'RUNNING', %(d)s)",
               {"id": run, "d": dream})
    a = at(db, "08:00")
    with pytest.raises(RuntimeError):
        a.post(run, "Title", "", "#Shorts", "private")
    with pytest.raises(ValueError):
        a.post(run, "Title", "", "#Shorts", "everyone")
    assert a.save_upload(run, YoutubeUploadResult(None, None, "public", "quota")) == "failed"
    assert db.rows("SELECT status FROM autopilot_run WHERE id = %(id)s", {"id": run})[0]["status"] == "POST_FAILED"
    # a failed upload can be posted again; the claim is atomic, so a second tap while posting is refused
    class Gw:
        started = []

        def start(self, wf, wid, arg):
            self.started.append((wf, arg))

    gw = Gw()
    a.post(run, "Title", "calm", "#Shorts #dream", "private", gw)
    assert gw.started == [("PostWorkflowPy", str(run))]
    with pytest.raises(RuntimeError):
        a.post(run, "Title", "calm", "#Shorts", "private", gw)
    assert a.save_upload(run, YoutubeUploadResult("abc", "https://youtube.com/shorts/abc", "private", None)) == "posted"
    assert db.rows("SELECT youtube_id FROM autopilot_run WHERE id = %(id)s", {"id": run})[0]["youtube_id"] == "abc"
    assert db.rows("SELECT count(*) AS n FROM cost_entry WHERE dream_short_id = %(d)s AND provider_id = 'youtube_api' AND amount_inr = 0",
                   {"d": dream})[0]["n"] == 1


def test_settings_are_validated(db):
    a = at(db, "08:00")
    s = a.settings()
    s.start_mode = "PRODUCER"
    with pytest.raises(ValueError):
        a.save_settings(s)
    s.start_mode, s.privacy = "REVIEW", "everyone"
    with pytest.raises(ValueError):
        a.save_settings(s)
    s.privacy, s.daily_cap_inr, s.shots, s.review_minutes, s.start_time = "unlisted", Decimal(20), 99, 0, "06:30"
    saved = a.save_settings(s)
    assert saved.start_mode == "REVIEW" and saved.shots == 8 and saved.review_minutes == 1 and saved.start_time == "06:30"
    assert saved.privacy == "unlisted" and saved.daily_cap_inr == Decimal(20)


# ------------------------------------------------------------------------------------------------ the daily cap (AutopilotDailyCapTest)


def dream(db, autopilot):
    d = uuid.uuid4()
    db.execute("INSERT INTO dream_short (id, idea, status) VALUES (%(id)s, 'x', 'RUNNING')", {"id": d})
    if autopilot:
        db.execute("INSERT INTO autopilot_run (id, run_date, status, dream_short_id) VALUES (%(id)s, CURRENT_DATE, 'RUNNING', %(d)s)",
                   {"id": uuid.uuid4(), "d": d})
    return d


def spend(db, d, inr):
    db.execute("INSERT INTO cost_entry (id, dream_short_id, provider_id, amount_inr) VALUES (%(id)s, %(d)s, 'gemini_api', %(a)s)",
               {"id": uuid.uuid4(), "d": d, "a": Decimal(inr)})


def test_an_autopilot_short_is_held_to_the_default_fifty_rupee_daily_cap(db):
    w = make_world(db)
    d = dream(db, True)
    assert w.producer.limit(d) == Decimal(50), "default daily cap 50 is below the 100 Short budget"
    spend(db, d, "40")
    assert w.producer.may_redo(str(d), 1)
    spend(db, d, "10")
    assert not w.producer.may_redo(str(d), 1), "50 spent: the autopilot Short may not redo or spend more"


def test_a_normal_short_uses_the_hundred_rupee_budget_and_asks_above_fifty(db):
    from studio.dream.producer import fits

    w = make_world(db)
    d = dream(db, False)
    ask, limit = Decimal(50), w.producer.limit(d)
    assert limit == Decimal(100)
    assert fits(Decimal(50), ask, Decimal(0), limit) and not fits(Decimal("50.01"), ask, Decimal(0), limit), "above 50: ask the owner"
    assert fits(Decimal(40), ask, Decimal(60), limit) and not fits(Decimal(41), ask, Decimal(60), limit), "over the 100 Short budget"


# ------------------------------------------------------------------------------------------------ the post workflow


def test_the_post_workflow_uploads_once_and_a_failure_goes_back_to_the_owner():
    pytest.importorskip("temporalio")
    from concurrent.futures import ThreadPoolExecutor

    from temporalio import activity
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from studio.workflow import queues
    from studio.workflow.autopilot_workflow import WORKFLOWS, AutopilotActivities, activity_functions

    class Fake:
        saved, uploads = [], []

        def upload_request(self, run_id):
            return {"runId": run_id, "videoUri": "s3://b/f.mp4", "title": "T", "description": "d", "tags": ["dream"], "privacy": "private"}

        def save_upload(self, run_id, result):
            self.saved.append(result)
            return "posted" if result["videoId"] else "failed"

    fake = Fake()
    outcomes = [{"videoId": "abc", "url": "u", "privacy": "private", "error": None}, "boom"]

    @activity.defn(name="youtube_upload")
    def upload(req):
        fake.uploads.append(req["title"])
        o = outcomes.pop(0)
        if isinstance(o, str):
            raise RuntimeError(o)
        return o

    async def main():
        env = await WorkflowEnvironment.start_time_skipping()
        with ThreadPoolExecutor(max_workers=4) as pool:
            async with Worker(env.client, task_queue=queues.CONTROL, workflows=WORKFLOWS, activities=activity_functions(fake), activity_executor=pool), \
                    Worker(env.client, task_queue=queues.MEDIA, activities=[upload], activity_executor=pool):
                try:
                    first = await env.client.execute_workflow("PostWorkflowPy", "run-1", id="post-1", task_queue=queues.CONTROL)
                    second = await env.client.execute_workflow("PostWorkflowPy", "run-1", id="post-2", task_queue=queues.CONTROL)
                    return first, second
                finally:
                    await env.shutdown()

    first, second = asyncio.run(main())
    assert (first, second) == ("posted", "failed")
    assert fake.uploads == ["T", "T"], "each Post tries the upload once: no automatic retry"
    assert fake.saved[1]["videoId"] is None and "boom" in fake.saved[1]["error"] and fake.saved[1]["privacy"] == "private"
    assert AutopilotActivities
