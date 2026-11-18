"""Every test runs with an isolated, unconfigured Gemini pool: no real keys, no shared cooldown file."""

import pytest

from workers import gemini_pool


@pytest.fixture(autouse=True)
def _isolated_gemini_pool(monkeypatch):
    monkeypatch.setattr(gemini_pool, "_projects", [])
    monkeypatch.setattr(gemini_pool, "_health", gemini_pool.Health(path=None))
