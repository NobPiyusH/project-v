"""Settings for the Python control plane. Same environment variables and defaults as the Java application.properties."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5433
    postgres_db: str = "projectv"
    postgres_user: str = "projectv"
    postgres_password: str = "projectv-local"
    config_dir: Path = REPO_ROOT / "config"
    s3_endpoint: str = "http://127.0.0.1:8333"
    s3_access_key: str = "projectv"
    s3_secret_key: str = "projectv-local-secret"
    s3_bucket: str = "projectv"
    # text models (same names and defaults as application.properties; keys only from the environment / .env)
    gemini_api_key: str = ""
    text_model: str = "gemini-3.6-flash"
    text_fallbacks: str = (
        "gemini-3.5-flash,nvidia:nvidia/nemotron-3-ultra-550b-a55b,nvidia:stepfun-ai/step-5-preview,groq:openai/gpt-oss-120b,"
        "openrouter:baidu/ernie-5.5-flash-preview:free,gemini-3.5-flash-lite,openrouter:google/gemma-4-31b-it:free"
    )
    story_model: str = "claude:claude-sonnet-5-5"
    agents_timeout_seconds: float = 75
    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    anthropic_api_key: str = ""
    claude_effort_overrides: str = ""
    aicredits_api_key: str = ""
    aicredits_base_url: str = "https://api.aicredits.in/v1"
    aicredits_fallback_enabled: bool = False

    @property
    def dsn(self) -> str:
        return (
            f"host={self.postgres_host} port={self.postgres_port} dbname={self.postgres_db} "
            f"user={self.postgres_user} password={self.postgres_password} connect_timeout=5"
        )

    @classmethod
    def from_env(cls) -> Settings:
        """POSTGRES_* and PROJECTV_CONFIG_DIR override the local defaults (secrets live in .env, never in code)."""
        e = os.environ
        return cls(
            postgres_host=e.get("POSTGRES_HOST", "127.0.0.1"),
            postgres_port=int(e.get("POSTGRES_PORT", "5433")),
            postgres_db=e.get("POSTGRES_DB", "projectv"),
            postgres_user=e.get("POSTGRES_USER", "projectv"),
            postgres_password=e.get("POSTGRES_PASSWORD", "projectv-local"),
            config_dir=Path(e.get("PROJECTV_CONFIG_DIR", str(REPO_ROOT / "config"))),
            s3_endpoint=e.get("S3_ENDPOINT", "http://127.0.0.1:8333"),
            s3_access_key=e.get("S3_ACCESS_KEY", "projectv"),
            s3_secret_key=e.get("S3_SECRET_KEY", "projectv-local-secret"),
            s3_bucket=e.get("S3_BUCKET", "projectv"),
            gemini_api_key=e.get("GEMINI_API_KEY", ""),
            text_model=e.get("GEMINI_MODEL", cls.text_model),
            text_fallbacks=e.get("GEMINI_TEXT_FALLBACKS", cls.text_fallbacks),
            story_model=e.get("CLAUDE_STORY_MODEL", cls.story_model),
            nvidia_api_key=e.get("NVIDIA_API_KEY", ""),
            groq_api_key=e.get("GROQ_API_KEY", ""),
            openrouter_api_key=e.get("OPENROUTER_API_KEY", ""),
            anthropic_api_key=e.get("ANTHROPIC_API_KEY", ""),
            claude_effort_overrides=e.get("CLAUDE_EFFORT_OVERRIDES", ""),
            aicredits_api_key=e.get("AICREDITS_API_KEY", ""),
            aicredits_base_url=e.get("AICREDITS_BASE_URL", cls.aicredits_base_url),
            aicredits_fallback_enabled=e.get("AICREDITS_FALLBACK_ENABLED", "false").lower() == "true",
        )
