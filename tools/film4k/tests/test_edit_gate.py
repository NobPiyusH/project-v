"""edit.py's gate step: exit 3 on a fail (hold for the owner), warns and crashes never block, flag off skips. Fake hook, no ffmpeg."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import edit  # noqa: E402

from tools.qualitygate import hook  # noqa: E402


def _patch(monkeypatch, status, enabled=True):
    seen = []
    monkeypatch.setattr(hook, "enabled", lambda value=None: enabled)
    monkeypatch.setattr(hook, "run_hook", lambda p, *a, **k: seen.append(p) or hook.HookOutcome(status, None, f"gate {status}"))
    return seen


def test_fail_holds_with_exit_3(monkeypatch, capsys):
    seen = _patch(monkeypatch, "fail")
    assert edit.gate(Path("act.mp4")) == 3 and seen == [Path("act.mp4")]
    assert "HOLD" in capsys.readouterr().out


def test_warn_and_error_do_not_block(monkeypatch):
    for st in ("warn", "pass", "error"):
        _patch(monkeypatch, st)
        assert edit.gate(Path("a.mp4")) == 0


def test_off_by_flag_or_option_skips_the_check(monkeypatch):
    seen = _patch(monkeypatch, "fail", enabled=False)
    assert edit.gate(Path("a.mp4")) == 0
    seen2 = _patch(monkeypatch, "fail")
    assert edit.gate(Path("a.mp4"), enabled=False) == 0 and not seen and not seen2
