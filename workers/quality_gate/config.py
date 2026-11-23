"""Gate settings from config/quality-gate.yaml; the OpenAI key is read from .env only and never printed."""
from __future__ import annotations

import functools
import os
from pathlib import Path

import yaml
from dotenv import dotenv_values

REPO = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO / "config" / "quality-gate.yaml"


@functools.cache
def load(path: str | None = None) -> dict:
    with open(path or CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def openai_key() -> str:
    """OPENAI_API_KEY from the environment, else from the main checkout's .env (worktrees have no .env of their own)."""
    key = os.getenv("OPENAI_API_KEY")
    if key:
        return key.strip()
    candidates = [REPO / ".env"]
    candidates += [p / ".env" for p in REPO.parents if (p / ".env").exists()][:1]
    for env in candidates:
        if env.exists():
            val = dotenv_values(env).get("OPENAI_API_KEY")
            if val:
                return val.strip().strip('"')
    raise RuntimeError("OPENAI_API_KEY is not set in .env")


def env_value(name: str, default: str | None = None) -> str | None:
    if os.getenv(name):
        return os.getenv(name)
    for p in [REPO, *REPO.parents]:
        env = p / ".env"
        if env.exists():
            v = dotenv_values(env).get(name)
            if v:
                return v
    return default
