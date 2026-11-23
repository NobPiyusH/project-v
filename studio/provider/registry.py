"""Loads config/providers.yaml into the provider table at startup. Port of ProviderRegistry."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml

from studio.db import Db

log = logging.getLogger(__name__)

_TOP_LEVEL = ("id", "name", "kind", "capabilities", "enabled")


class ProviderRegistry:
    def __init__(self, db: Db, config_dir: str | Path):
        self._db = db
        self._file = Path(config_dir) / "providers.yaml"

    def reload(self) -> int:
        with open(self._file, encoding="utf-8") as f:
            providers: list[dict[str, Any]] = yaml.safe_load(f)["providers"]
        for p in providers:
            config = {k: v for k, v in p.items() if k not in _TOP_LEVEL}
            self._db.execute(
                """
                INSERT INTO provider (id, name, kind, capabilities_json, config_json, enabled)
                VALUES (%(id)s, %(name)s, %(kind)s, CAST(%(caps)s AS jsonb), CAST(%(config)s AS jsonb), %(enabled)s)
                ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, kind = EXCLUDED.kind,
                    capabilities_json = EXCLUDED.capabilities_json, config_json = EXCLUDED.config_json,
                    enabled = EXCLUDED.enabled, updated_at = now()
                """,
                {
                    "id": p.get("id"),
                    "name": p.get("name"),
                    "kind": p.get("kind"),
                    "caps": json.dumps(p.get("capabilities", [])),
                    "config": json.dumps(config),
                    "enabled": p.get("enabled", True) is True,
                },
            )
        log.info("Provider registry loaded: %d providers from %s", len(providers), self._file)
        return len(providers)

    def all(self) -> list[dict[str, Any]]:
        return self._db.rows(
            "SELECT id, name, kind, capabilities_json::text AS capabilities, config_json::text AS config, enabled "
            "FROM provider ORDER BY kind, id"
        )
