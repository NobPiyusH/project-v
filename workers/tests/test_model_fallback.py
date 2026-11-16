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
