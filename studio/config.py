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

    @property
    def dsn(self) -> str:
        return (
            f"host={self.postgres_host} port={self.postgres_port} dbname={self.postgres_db} "
            f"user={self.postgres_user} password={self.postgres_password} connect_timeout=5"
        )

    @staticmethod
    def from_env() -> Settings:
        """POSTGRES_* and PROJECTV_CONFIG_DIR override the local defaults (secrets live in .env, never in code)."""
        e = os.environ
        return Settings(
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
        )
