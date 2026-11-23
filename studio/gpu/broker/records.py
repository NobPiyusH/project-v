"""What the broker leaves in the ledger (CLAUDE.md rule 6): a RoutingDecision per acquire, a CostEntry per rental. Port of BrokerRecords."""

from __future__ import annotations

import json
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol

from studio.agents.jfmt import fixed
from studio.db import Db
from studio.ledger import Pricing
from studio.ledger.pricing import dec

_FOUR = Decimal("0.0001")


class BrokerRecords(Protocol):
    def decision(self, dream_short_id: uuid.UUID, capability: str, considered: list[dict[str, Any]], chosen: str | None, reason: str) -> None: ...

    def cost(self, dream_short_id: uuid.UUID | None, provider: str, hours: float, hourly_usd: float, note: str) -> None: ...


class PgBrokerRecords:
    def __init__(self, db: Db, pricing: Pricing):
        self._db, self._pricing = db, pricing

    def _is_dream(self, id_: uuid.UUID | None) -> bool:
        if id_ is None:
            return False
        return self._db.rows("SELECT count(*) AS n FROM dream_short WHERE id = %(d)s", {"d": id_})[0]["n"] > 0

    def decision(self, dream_short_id, capability, considered, chosen, reason) -> None:
        if not self._is_dream(dream_short_id):
            return  # a Video's rental: the Video stage logs its own routing decision (rule 6)
        self._db.execute(
            """
            INSERT INTO routing_decision (id, dream_short_id, stage, capability, candidates_json, chosen_provider_id, reason_text)
            VALUES (%(id)s, %(d)s, 'DREAM_GPU', %(c)s, CAST(%(cands)s AS jsonb), %(chosen)s, %(r)s)
            """,
            {"id": uuid.uuid4(), "d": dream_short_id, "c": capability, "cands": json.dumps(considered, default=float), "chosen": chosen, "r": reason},
        )

    def cost(self, dream_short_id, provider, hours, hourly_usd, note) -> None:
        inr = dec(hours * hourly_usd * self._pricing.fx()).quantize(_FOUR, rounding=ROUND_HALF_UP)
        # The id is a Dream Short, or a Video that rented the shared pod: a Video's rental is booked on the Video's own ledger;
        # the canary has no Short at all.
        dream = self._is_dream(dream_short_id)
        self._db.execute(
            """
            INSERT INTO cost_entry (id, video_id, dream_short_id, provider_id, amount_inr, units, unit_type, estimated, note)
            VALUES (%(id)s, %(v)s, %(d)s, %(p)s, %(amt)s, %(u)s, 'gpu_hour', true, %(note)s)
            """,
            {"id": uuid.uuid4(), "v": None if dream else dream_short_id, "d": dream_short_id if dream else None, "p": provider,
             "amt": inr, "u": dec(hours).quantize(_FOUR, rounding=ROUND_HALF_UP), "note": f"{note} (${fixed(hourly_usd, 4)}/h live)"},
        )
