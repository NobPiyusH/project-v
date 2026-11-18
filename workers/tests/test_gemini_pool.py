"""Gemini multi-project failover: the 12 scenarios of the owner's spec, with mocked errors (no real quota used)."""

import threading

import pytest

from workers import gemini_pool as G


class ApiError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(f"{code} {message}")
        self.code, self.details = code, details or {}


RPM_429 = lambda: ApiError(429, "RESOURCE_EXHAUSTED", {"error": {"details": [  # noqa: E731
    {"violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel"}]}, {"retryDelay": "7s"}]}})
TPM_429 = lambda: ApiError(429, "RESOURCE_EXHAUSTED", {"error": {"details": [  # noqa: E731
    {"violations": [{"quotaId": "GenerateContentInputTokensPerModelPerMinute"}]}]}})
RPD_429 = lambda: ApiError(429, "RESOURCE_EXHAUSTED", {"error": {"details": [  # noqa: E731
    {"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel"}]}]}})
SPEND = lambda: ApiError(429, "Your billing account has an outstanding balance")  # noqa: E731
BAD_KEY = lambda: ApiError(400, "API_KEY_INVALID: API key not valid")  # noqa: E731


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def pool():
    return [G.Project("a", "proj-a", 1, ["ka"]), G.Project("b", "proj-b", 2, ["kb"])]


def runner(script):
    """fn() whose behaviour per project comes from script[project_id] (exception factory or value)."""
    calls = []

    def fn():
        key = G.current_key()
        calls.append(key)
        outcome = script[{"ka": "proj-a", "kb": "proj-b", "ka2": "proj-a"}[key]]
        if callable(outcome):
            raise outcome()
        return outcome
    return fn, calls


def test_1_primary_project_succeeds():
    fn, calls = runner({"proj-a": "ok", "proj-b": "never"})
    assert G.execute(fn, "t", pool=pool(), hp=G.Health(path=None)) == "ok" and calls == ["ka"]


def test_2_rpm_429_fails_over_and_cools_the_primary_for_the_server_delay():
    hp = G.Health(path=None, clock=Clock())
    fn, calls = runner({"proj-a": RPM_429, "proj-b": "ok"})
    assert G.execute(fn, "t", pool=pool(), hp=hp) == "ok" and calls == ["ka", "kb"]
    assert hp.state["proj-a"]["last"] == G.RPM and hp.state["proj-a"]["until"] - hp.clock() == pytest.approx(7)


def test_3_tpm_429_fails_over():
    hp = G.Health(path=None)
    fn, calls = runner({"proj-a": TPM_429, "proj-b": "ok"})
    assert G.execute(fn, "t", pool=pool(), hp=hp) == "ok" and hp.state["proj-a"]["last"] == G.TPM


def test_4_daily_quota_cools_until_reset_and_does_not_fail_over():
    hp = G.Health(path=None, clock=Clock())
    fn, calls = runner({"proj-a": RPD_429, "proj-b": "ok"})
    with pytest.raises(ApiError):
        G.execute(fn, "t", pool=pool(), hp=hp)
    assert calls == ["ka"], "daily quota is not circumvented on another project"
    assert hp.state["proj-a"]["last"] == G.RPD and hp.state["proj-a"]["until"] - hp.clock() > 60


def test_5_spend_cap_or_billing_restriction_does_not_fail_over():
    hp = G.Health(path=None)
    fn, calls = runner({"proj-a": SPEND, "proj-b": "ok"})
    with pytest.raises(ApiError):
        G.execute(fn, "t", pool=pool(), hp=hp)
    assert calls == ["ka"] and hp.state["proj-a"]["last"] == G.SPEND


def test_6_invalid_key_disables_that_project_without_failing_over():
    hp = G.Health(path=None)
    fn, calls = runner({"proj-a": BAD_KEY, "proj-b": "ok"})
    with pytest.raises(ApiError):
        G.execute(fn, "t", pool=pool(), hp=hp)
    assert calls == ["ka"] and not hp.available("proj-a")


def test_7_all_projects_unavailable_returns_a_structured_error():
    fn, _ = runner({"proj-a": RPM_429, "proj-b": RPM_429})
    with pytest.raises(G.NoEligibleProject) as e:
        G.execute(fn, "t", pool=pool(), hp=G.Health(path=None))
    assert [o["class"] for o in e.value.outcomes] == [G.RPM, G.RPM]
    assert all("ka" not in str(o) and "kb" not in str(o) for o in e.value.outcomes), "no keys in the error"


def test_8_project_recovers_after_its_cooldown():
    clock = Clock()
    hp = G.Health(path=None, clock=clock)
    fn, _ = runner({"proj-a": RPM_429, "proj-b": "ok"})
    G.execute(fn, "t", pool=pool(), hp=hp)
    assert not hp.available("proj-a")
    clock.t += 8
    fn2, calls = runner({"proj-a": "ok", "proj-b": "x"})
    assert G.execute(fn2, "t", pool=pool(), hp=hp) == "ok" and calls == ["ka"]


def test_9_concurrent_requests_share_one_consistent_health_view():
    hp = G.Health(path=None)
    fn, calls = runner({"proj-a": RPM_429, "proj-b": "ok"})
    results = []
    threads = [threading.Thread(target=lambda: results.append(G.execute(fn, "t", pool=pool(), hp=hp))) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results == ["ok"] * 8
    assert calls.count("ka") <= 8 and not hp.available("proj-a"), "primary cooled once it hit its limit"


def test_10_no_infinite_retries():
    many = [G.Project(f"p{i}", f"proj-{i}", i, [f"k{i}"]) for i in range(10)]
    tries = []

    def fn():
        tries.append(1)
        raise RPM_429()
    with pytest.raises(G.NoEligibleProject):
        G.execute(fn, "t", pool=many, hp=G.Health(path=None))
    assert len(tries) == G.MAX_FAILOVERS + 1


def test_11_keys_of_the_same_project_are_one_quota_pool():
    env = {"GEMINI_PROJECTS": "main:proj-a:1,main2:proj-a:2,b:proj-b:3",
           "GEMINI_KEY_MAIN": "ka", "GEMINI_KEY_MAIN2": "ka2", "GEMINI_KEY_B": "kb"}
    projects = G.load_projects(env)
    assert [p.project_id for p in projects] == ["proj-a", "proj-b"]
    assert sorted(projects[0].keys) == ["ka", "ka2"]
    hp = G.Health(path=None)
    fn, calls = runner({"proj-a": RPM_429, "proj-b": "ok"})
    G.execute(fn, "t", pool=projects, hp=hp)
    assert len([c for c in calls if c in ("ka", "ka2")]) == 1, "the second key of a cooled project is not tried"


def test_12_the_request_is_identical_on_every_project():
    seen = []
    request = {"model": "gemini-x", "contents": ["prompt", b"image-bytes"], "config": {"system": "s", "schema": "S"}}

    def fn():
        seen.append(repr(request))
        if G.current_key() == "ka":
            raise RPM_429()
        return "ok"
    G.execute(fn, "t", pool=pool(), hp=G.Health(path=None))
    assert len(seen) == 2 and seen[0] == seen[1]


def test_timeouts_are_not_retried_on_another_project():
    fn, calls = runner({"proj-a": lambda: TimeoutError("read timed out"), "proj-b": "ok"})
    with pytest.raises(TimeoutError):
        G.execute(fn, "t", pool=pool(), hp=G.Health(path=None))
    assert calls == ["ka"], "outcome unknown: no duplicate billable request"
