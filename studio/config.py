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
    story_model: str = "claude:claude-sonnet-5-5"  # the Director: concept and shot plan
    script_model: str = "openai:gpt-5.4-mini"  # the script writer (owner A/B 23 Nov 2026); SCRIPT_MODEL=claude:claude-sonnet-5-5 switches back
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_price_in_usd_per_1m: float = 0.75  # list prices, re-verified 23 Nov 2026: re-check before changing
    openai_price_out_usd_per_1m: float = 4.50
    inr_per_usd: float = 90.0  # the rupee rate OpenAI calls are booked at (projectv.fx.inr-per-usd)
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
    # GPU pods (application.properties defaults: those win over the Java @Value defaults)
    runpod_api_key: str = ""
    runpod_base_url: str = "https://api.runpod.io/v2"
    gpu_watchdog_dry_run: bool = False
    gpu_watchdog_poll_seconds: int = 180
    gpu_pod_prefix: str = "projectv-"
    gpu_max_runtime_minutes: int = 180
    gpu_idle_minutes: int = 15
    gpu_lease_grace_minutes: int = 10
    gpu_max_pods: int = 1
    gpu_max_hourly_usd: float = 2.0
    gpu_watchdog_python: bool = False  # the Python watchdog loop inside this app (the only watchdog since Java was removed)
    # The plain dataclass keeps these OFF so unit tests build a quiet app; from_env() turns them ON (Python is the live control plane since the
    # Java removal, docs/java-removal.md): set STUDIO_DREAM_ROUTES, STUDIO_VIDEO_ROUTES, STUDIO_JOBS, STUDIO_GPU_WATCHDOG to "false" to switch one off.
    dream_routes: bool = False  # serve /api/dream, /api/autopilot and the Dream GPU routes (STUDIO_DREAM_ROUTES)
    video_routes: bool = False  # serve the video pipeline routes: /api/videos, /api/shots, /api/producer, /api/budget... (STUDIO_VIDEO_ROUTES)
    jobs: bool = False  # run the periodic jobs: pod release checks every 30 s, autopilot tick every minute (STUDIO_JOBS)
    temporal_address: str = "127.0.0.1:7233"
    dream_gpu_provider: str = "modal"  # modal (default, no pod) or broker (rented pods): projectv.dream.gpu-provider
    youtube_token_file: str = ".secrets/youtube-token.json"
    autopilot_zone: str = "Asia/Kolkata"
    port: int = 8080  # the dashboard and tools/ already talk to :8080
    migrate_on_start: bool = False  # apply db/migration/V*.sql at start (Flyway-compatible); from_env() turns it on

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
            script_model=e.get("SCRIPT_MODEL", cls.script_model),
            openai_api_key=e.get("OPENAI_API_KEY", ""),
            openai_base_url=e.get("OPENAI_BASE_URL", cls.openai_base_url),
            openai_price_in_usd_per_1m=float(e.get("OPENAI_PRICE_IN", "0.75")),
            openai_price_out_usd_per_1m=float(e.get("OPENAI_PRICE_OUT", "4.50")),
            nvidia_api_key=e.get("NVIDIA_API_KEY", ""),
            groq_api_key=e.get("GROQ_API_KEY", ""),
            openrouter_api_key=e.get("OPENROUTER_API_KEY", ""),
            anthropic_api_key=e.get("ANTHROPIC_API_KEY", ""),
            claude_effort_overrides=e.get("CLAUDE_EFFORT_OVERRIDES", ""),
            aicredits_api_key=e.get("AICREDITS_API_KEY", ""),
            aicredits_base_url=e.get("AICREDITS_BASE_URL", cls.aicredits_base_url),
            aicredits_fallback_enabled=e.get("AICREDITS_FALLBACK_ENABLED", "false").lower() == "true",
            runpod_api_key=e.get("RUNPOD_API_KEY", ""),
            runpod_base_url=e.get("RUNPOD_BASE_URL", cls.runpod_base_url),
            gpu_watchdog_dry_run=e.get("GPU_WATCHDOG_DRY_RUN", "false").lower() == "true",
            gpu_watchdog_poll_seconds=int(e.get("GPU_WATCHDOG_POLL_SECONDS", "180")),
            gpu_pod_prefix=e.get("GPU_POD_PREFIX", "projectv-"),
            gpu_max_runtime_minutes=int(e.get("GPU_MAX_RUNTIME_MINUTES", "180")),
            gpu_idle_minutes=int(e.get("GPU_IDLE_MINUTES", "15")),
            gpu_lease_grace_minutes=int(e.get("GPU_LEASE_GRACE_MINUTES", "10")),
            gpu_max_pods=int(e.get("GPU_MAX_PODS", "1")),
            gpu_max_hourly_usd=float(e.get("GPU_MAX_HOURLY_USD", "2.0")),
            gpu_watchdog_python=e.get("STUDIO_GPU_WATCHDOG", "true").lower() == "true",
            dream_routes=e.get("STUDIO_DREAM_ROUTES", "true").lower() == "true",
            video_routes=e.get("STUDIO_VIDEO_ROUTES", "true").lower() == "true",
            jobs=e.get("STUDIO_JOBS", "true").lower() == "true",
            temporal_address=e.get("TEMPORAL_ADDRESS", "127.0.0.1:7233"),
            dream_gpu_provider=e.get("DREAM_GPU_PROVIDER", "modal"),
            youtube_token_file=e.get("YOUTUBE_TOKEN_FILE", ".secrets/youtube-token.json"),
            autopilot_zone=e.get("AUTOPILOT_ZONE", "Asia/Kolkata"),
            port=int(e.get("PORT", e.get("STUDIO_PORT", "8080"))),
            migrate_on_start=e.get("STUDIO_MIGRATE", "true").lower() == "true",
        )
