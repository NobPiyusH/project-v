"""Work X item 5: ordered model fallback for Gemini calls."""

import pytest

from workers import common


class Retired(Exception):
    code = 404


class Billing(Exception):
    code = 403


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(common.time, "sleep", lambda s: None)


def test_retired_model_falls_back_to_the_next():
    calls = []

    def call(m):
        calls.append(m)
        if m == "old":
            raise Retired("models/old is no longer available to new users")
        return "ok"

    used, result = common.with_models("old, new", call, what="test")
    assert (used, result) == ("new", "ok")
    assert calls == ["old", "new"]


def test_billing_or_quota_errors_do_not_hop_models():
    calls = []

    def call(m):
        calls.append(m)
        raise Billing("permission denied: billing account disabled")

    with pytest.raises(Billing):
        common.with_models("a,b", call, what="test")
    assert calls == ["a"], "a billing problem is not a model problem: don't spend on the next model"


def test_last_model_error_is_raised():
    def call(m):
        raise Retired("not found")

    with pytest.raises(Retired):
        common.with_models("a,b", call, what="test")


def test_model_list_parsing_and_default():
    assert common.model_list(" a , b ,, ") == ["a", "b"]
    assert common.model_list(None, default="x") == ["x"]


def test_closed_client_is_rebuilt_and_retried(monkeypatch):
    resets = []
    monkeypatch.setattr(common, "_reset_gemini", lambda: resets.append(1))
    calls = []

    def fn():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("Cannot send a request, as the client has been closed.")
        return "ok"

    assert common.with_retry(fn, what="test") == "ok"
    assert resets == [1]


class Quota(Exception):
    code = 429


def test_used_up_daily_quota_moves_to_next_model_instead_of_sleeping(monkeypatch):
    from google.genai import errors as genai_errors
    monkeypatch.setattr(common.genai_errors, "ClientError", Quota)
    calls = []

    def call(m):
        calls.append(m)
        if m == "tts-a":
            raise Quota("429 RESOURCE_EXHAUSTED {'retryDelay': '51767s'}")
        return "ok"

    assert common.with_models("tts-a,tts-b", call, what="TTS") == ("tts-b", "ok")
    assert calls == ["tts-a", "tts-b"]
