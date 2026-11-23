"""Every money- or quota-spending job writes a CostEntry (HANDOVER §8, §21). Port of CostLedger. Free calls are logged at ₹0."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from studio.db import Db


class CostLedger:
    def __init__(self, db: Db):
        self._db = db

    def record(
        self,
        video_id: uuid.UUID | None,
        provider_id: str,
        amount_inr: Decimal,
        units: Decimal | None,
        unit_type: str | None,
        estimated: bool,
        note: str | None,
    ) -> None:
        # One transaction (Java ran two separate statements): the entry and the video total can never disagree.
        with self._db.transaction():
            self._db.execute(
                """
                INSERT INTO cost_entry (id, video_id, provider_id, amount_inr, units, unit_type, estimated, note)
                VALUES (%(id)s, %(video)s, %(provider)s, %(amount)s, %(units)s, %(unit_type)s, %(estimated)s, %(note)s)
                """,
                {
                    "id": uuid.uuid4(),
                    "video": video_id,
                    "provider": provider_id,
                    "amount": amount_inr,
                    "units": units,
                    "unit_type": unit_type,
                    "estimated": estimated,
                    "note": note,
                },
            )
            self._db.execute(
                "UPDATE video SET spent_inr = spent_inr + %(amount)s, updated_at = now() WHERE id = %(video)s",
                {"amount": amount_inr, "video": video_id},
            )

    def totals_by_provider(self) -> list[dict[str, Any]]:
        return self._db.rows(
            """
            SELECT provider_id, count(*) AS entries, sum(amount_inr) AS amount_inr, sum(units) AS units,
                   min(unit_type) AS unit_type
            FROM cost_entry GROUP BY provider_id ORDER BY provider_id
            """
        )

    def for_video(self, video_id: uuid.UUID) -> list[dict[str, Any]]:
        return self._db.rows(
            """
            SELECT provider_id, amount_inr, units, unit_type, estimated, note, ts
            FROM cost_entry WHERE video_id = %(video)s ORDER BY ts
            """,
            {"video": video_id},
        )
