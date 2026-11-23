"""Every generated asset gets an asset row AND a provenance row (HANDOVER §8, §22). No orphans. Port of AssetStore."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from studio.db import Db


def _lic(n: str) -> uuid.UUID:
    return uuid.UUID(f"00000000-0000-0000-0000-00000000a{n}")


LICENCE_GEMINI = _lic("001")
LICENCE_ZIMAGE = _lic("002")
LICENCE_OWN_RENDER = _lic("003")
LICENCE_WAN = _lic("004")
LICENCE_NASA = _lic("005")
LICENCE_RIFE = _lic("006")
LICENCE_SEEDVR2 = _lic("007")
LICENCE_ELEVENLABS_FREE = _lic("008")
LICENCE_KOKORO = _lic("009")
LICENCE_COMMONS = _lic("010")
LICENCE_CHATTERBOX = _lic("011")
LICENCE_FLUX2_KLEIN = _lic("013")
LICENCE_PEXELS = _lic("020")
LICENCE_PIXABAY = _lic("021")
LICENCE_ARCHIVE = _lic("022")


@dataclass
class NewAsset:
    video_id: uuid.UUID | None
    kind: str
    uri: str
    mime_type: str | None
    width: int | None
    height: int | None
    duration_sec: float | None
    provider: str
    model: str | None
    prompt: str | None
    seed: int | None
    licence_id: uuid.UUID | None
    ai_generated: bool


class AssetStore:
    def __init__(self, db: Db):
        self._db = db

    def create(self, a: NewAsset) -> uuid.UUID:
        provenance_id, asset_id = uuid.uuid4(), uuid.uuid4()
        with self._db.transaction():
            self._db.execute(
                """
                INSERT INTO provenance (id, provider, model, prompt, seed, licence_id, ai_generated, created_by)
                VALUES (%(id)s, %(provider)s, %(model)s, %(prompt)s, %(seed)s, %(licence)s, %(ai)s, 'projectv')
                """,
                {"id": provenance_id, "provider": a.provider, "model": a.model, "prompt": a.prompt, "seed": a.seed,
                 "licence": a.licence_id, "ai": a.ai_generated},
            )
            self._db.execute(
                """
                INSERT INTO asset (id, video_id, kind, uri_hot, mime_type, width, height, duration_sec, provenance_id)
                VALUES (%(id)s, %(video)s, %(kind)s, %(uri)s, %(mime)s, %(w)s, %(h)s, %(dur)s, %(prov)s)
                """,
                {"id": asset_id, "video": a.video_id, "kind": a.kind, "uri": a.uri, "mime": a.mime_type, "w": a.width,
                 "h": a.height, "dur": a.duration_sec, "prov": provenance_id},
            )
            self._db.execute(
                "UPDATE provenance SET asset_id = %(a)s WHERE id = %(p)s", {"a": asset_id, "p": provenance_id}
            )
        return asset_id

    def uri_of(self, asset_id: uuid.UUID) -> str:
        return self._one("SELECT uri_hot FROM asset WHERE id = %(id)s", asset_id)["uri_hot"]

    def describe(self, asset_id: uuid.UUID) -> dict[str, Any]:
        return self._one(
            """
            SELECT a.id, a.kind, a.uri_hot, a.mime_type, a.width, a.height, a.duration_sec,
                   p.provider, p.model, p.prompt, p.seed, l.name AS licence,
                   p.source_url, p.source_id, p.author, p.attribution_text, p.credit_required, p.fetched_at
            FROM asset a LEFT JOIN provenance p ON p.id = a.provenance_id LEFT JOIN licence l ON l.id = p.licence_id
            WHERE a.id = %(id)s
            """,
            asset_id,
        )

    def _one(self, sql: str, asset_id: uuid.UUID) -> dict[str, Any]:
        rows = self._db.rows(sql, {"id": asset_id})
        if len(rows) != 1:  # Java's .single() fails the same way
            raise LookupError(f"asset {asset_id}: expected 1 row, got {len(rows)}")
        return rows[0]
