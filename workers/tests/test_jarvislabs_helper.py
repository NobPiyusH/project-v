"""jarvislabs_helper against a fake SDK client: no network, no key, no instance."""
import importlib.util
import sys
import types
from pathlib import Path

import pytest


class _M:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self):
        return dict(self.__dict__)


class FakeClient:
    destroyed: list = []
    created: list = []

    def __init__(self):
        self.account = types.SimpleNamespace(
            currency=lambda: "INR",
            balance=lambda: _M(balance=500.0, grants=0.0),
            gpu_availability=lambda: [_M(gpu_type="A100", region="IN2", num_free_devices=2, price_per_hour=84.24, vram="40", workload_type="container")],
        )
        self.instances = types.SimpleNamespace(
            destroy=lambda i: FakeClient.destroyed.append(i),
            create=lambda **kw: (FakeClient.created.append(kw), _M(machine_id=9, name=kw["name"], status="Running", endpoints=["x.jarvislabs.net"]))[1],
        )
        self.scripts = types.SimpleNamespace(
            add=lambda *a, **k: True,
            list=lambda: [_M(script_name="projectv-a", script_id=3)],
            remove=lambda i: None,
        )

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture()
def helper(monkeypatch):
    monkeypatch.setitem(sys.modules, "jarvislabs", types.SimpleNamespace(Client=FakeClient))
    spec = importlib.util.spec_from_file_location("jl_helper", Path(__file__).parents[1] / "jarvislabs_helper.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_gpus_carry_the_account_currency(helper):
    rows = helper.main(["h", "gpus"])
    assert rows[0]["currency"] == "INR" and rows[0]["price_per_hour"] == 84.24


def test_balance_is_read_only(helper):
    assert helper.main(["h", "balance"]) == {"balance": 500.0, "grants": 0.0, "currency": "INR"}


def test_create_refuses_names_without_the_studio_prefix(helper):
    FakeClient.created.clear()
    import io
    sys.stdin = io.StringIO('{"name":"other","gpu_type":"A100","storage":40,"script":"#!/bin/bash"}')
    with pytest.raises(ValueError):
        helper.main(["h", "create"])
    assert FakeClient.created == []
