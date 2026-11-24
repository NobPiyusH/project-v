"""quality_gate_check activity with the storage calls faked: a fail raises a non-retryable error, a warn returns, the report is stored."""

import pytest

pytest.importorskip("temporalio")

from temporalio.exceptions import ApplicationError  # noqa: E402

from tools.qualitygate.hook import HookOutcome  # noqa: E402
from workers.activities import gate_check as g  # noqa: E402


def _fake(monkeypatch, outcome):
    puts = []
    monkeypatch.setattr(g, "download", lambda uri, dest: dest)
    monkeypatch.setattr(g, "put_file", lambda key, path, ct: puts.append(key) or f"s3://b/{key}")

    def run(master, report, srt):
        report.write_text("{}", encoding="utf-8")
        outcome.report_path = report
        return outcome

    monkeypatch.setattr(g, "run_hook", run)
    return puts


def test_warn_returns_summary_and_stores_report(monkeypatch):
    puts = _fake(monkeypatch, HookOutcome("warn", None, "WARN: motion low", warnings=["motion low"]))
    r = g.quality_gate_check({"videoId": "v1", "masterUri": "s3://b/m.mp4"})
    assert r["status"] == "warn" and r["reportUri"] == "s3://b/videos/v1/master/quality-gate.json"
    assert puts == ["videos/v1/master/quality-gate.json"]


def test_fail_raises_non_retryable_after_storing_the_report(monkeypatch):
    puts = _fake(monkeypatch, HookOutcome("fail", None, "FAIL: black", failures=["black"]))
    with pytest.raises(ApplicationError) as e:
        g.quality_gate_check({"videoId": "v1", "masterUri": "s3://b/m.mp4", "srtUri": "s3://b/c.srt"})
    assert e.value.non_retryable and e.value.type == g.GATE_FAILED and puts, "the report is kept for the owner"
