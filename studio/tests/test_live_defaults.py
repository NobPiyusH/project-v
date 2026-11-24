"""Python is the live control plane (docs/java-removal.md): from the environment everything is on, port 8080, one watchdog per database."""

import pytest

from studio.config import Settings
from studio.singleton import JOBS, WATCHDOG, Singleton


def test_from_env_turns_the_live_pieces_on_and_each_can_be_switched_off(monkeypatch):
    for k in ("STUDIO_DREAM_ROUTES", "STUDIO_VIDEO_ROUTES", "STUDIO_JOBS", "STUDIO_GPU_WATCHDOG", "STUDIO_MIGRATE", "PORT", "STUDIO_PORT", "DREAM_GPU_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    s = Settings.from_env()
    assert s.dream_routes and s.video_routes and s.jobs and s.gpu_watchdog_python and s.migrate_on_start
    assert s.port == 8080 and s.dream_gpu_provider == "modal", "no pod can be rented until the owner switches the provider"
    monkeypatch.setenv("STUDIO_JOBS", "false")
    monkeypatch.setenv("PORT", "9000")
    assert Settings.from_env().jobs is False and Settings.from_env().port == 9000


def test_only_one_process_holds_a_lock_and_a_crashed_holder_frees_it(scratch_dsn):
    pytest.importorskip("psycopg")
    a, b = Singleton(scratch_dsn, WATCHDOG, "the GPU watchdog"), Singleton(scratch_dsn, WATCHDOG, "the GPU watchdog")
    assert a.try_acquire() is True and a.try_acquire() is True
    assert b.try_acquire() is False, "a second watchdog must not start"
    assert Singleton(scratch_dsn, JOBS, "jobs").try_acquire() is True, "the jobs lock is separate"
    a.release()
    assert b.try_acquire() is True
    b.release()


def test_an_unreachable_database_means_not_started_not_a_crash():
    assert Singleton("host=127.0.0.1 port=1 dbname=x user=x connect_timeout=1", WATCHDOG, "w").try_acquire() is False
