"""Dream Short quality check: the checklist reaches the model, and a failed check never stops a Short."""

from workers.dream import check


def test_the_prompt_carries_the_brief_and_every_checklist_item():
    p = check.prompt("Walk up a path to a vending machine; 5 shots")
    assert "Walk up a path" in p
    for key in check.CHECKLIST:
        assert key in p
    assert "No people" in p and "garbled" in p


def test_a_failed_check_returns_an_empty_report_instead_of_raising(monkeypatch):
    monkeypatch.setattr(check, "check", lambda req, tmp: (_ for _ in ()).throw(RuntimeError("no network")))
    r = check.dream_check.__wrapped__({"dreamId": "d", "videoUri": "s3://x"}) if hasattr(check.dream_check, "__wrapped__") \
        else check.dream_check({"dreamId": "d", "videoUri": "s3://x"})
    assert r["overall"] == 0 and r["issues"] == [] and "no network" in r["error"]
