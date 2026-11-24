"""lease.keep must fail LOUDLY when the lease cannot be opened (24 Nov 2026: Docker was off and it died without a word)."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("lease", Path(__file__).resolve().parents[1] / "lease.py")
lease = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lease)


def test_keep_exits_2_with_a_clear_message_when_the_database_is_down(monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("docker is not running")

    monkeypatch.setattr(lease, "open_lease", boom)
    with pytest.raises(SystemExit) as e:
        lease.keep("runpod", "pod123", "projectv-x", "t", tries=3, wait_s=0)
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "NO LEASE" in err and err.count("lease open failed") == 3


def test_keep_retries_until_the_database_comes_up(monkeypatch):
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("starting")

    monkeypatch.setattr(lease, "open_lease", flaky)
    monkeypatch.setattr(lease, "release", lambda pod: None)
    monkeypatch.setattr(lease.time, "sleep", lambda s: None)
    lease.keep("runpod", "pod123", "projectv-x", "t", max_minutes=0, tries=5, wait_s=0)
    assert calls["n"] == 3
