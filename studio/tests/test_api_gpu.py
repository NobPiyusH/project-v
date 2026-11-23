"""GPU routes on the real app factory with fakes: no key means no RunPod calls."""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from studio.api.app import create_app  # noqa: E402
from studio.config import Settings  # noqa: E402
from studio.tests.fakes import FakeDb  # noqa: E402


def client(**kw):
    return TestClient(create_app(db=FakeDb(), settings=Settings(**kw)))


def test_watchdog_status_before_the_first_sweep_matches_the_java_shape():
    j = client().get("/api/gpu/watchdog").json()
    assert set(j) == {"limits", "lastSweep", "lastSweepVast", "leases", "terminations"}
    assert j["lastSweep"] is None and j["limits"]["prefix"] == "projectv-" and j["limits"]["maxPods"] == 1 and j["limits"]["dryRun"] is False


def test_pods_without_a_key_is_not_configured_and_logs_report_it():
    c = client()
    assert c.get("/api/gpu/pods").json() == {"configured": False, "pods": []}
    assert c.get("/api/gpu/pods/abcd1234/logs").json()["error"] == "No RunPod key is configured on the control plane"
