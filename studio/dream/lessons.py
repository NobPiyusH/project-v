"""Learning loop phases 3-4: owner reviews become proposed lessons. Port of DreamLessons."""

from __future__ import annotations

import uuid
from typing import Any

from studio.db import Db

TARGETS = ["WRITER", "PICTURE", "ALL"]
STATUSES = ["PROPOSED", "ACTIVE", "RETIRED"]


def _blank_to_none(s: str | None) -> str | None:
    return None if s is None or not s.strip() else s.strip()


class DreamLessons:
    def __init__(self, db: Db):
        self.db = db

    def review(self, dream: uuid.UUID, shot: uuid.UUID | None, rating: int, note: str | None) -> uuid.UUID:
        if rating < 1 or rating > 5:
            raise ValueError("Rating is 1 to 5")
        rid = uuid.uuid4()
        with self.db.transaction():
            self.db.execute("INSERT INTO dream_review (id, dream_short_id, shot_id, rating, note) "
                            "VALUES (%(id)s, %(d)s, %(s)s, %(r)s, %(n)s)",
                            {"id": rid, "d": dream, "s": shot, "r": rating, "n": _blank_to_none(note)})
            if _blank_to_none(note) is not None:
                self.db.execute("INSERT INTO dream_lesson (id, rule, target, review_id) VALUES (%(id)s, %(rule)s, %(t)s, %(r)s)",
                                {"id": uuid.uuid4(), "rule": note.strip(), "t": "ALL" if shot is None else "WRITER", "r": rid})
        return rid

    def reviews(self, dream: uuid.UUID) -> list[dict[str, Any]]:
        return self.db.rows("SELECT id, shot_id, rating, note, created_at FROM dream_review WHERE dream_short_id = %(d)s "
                            "ORDER BY created_at", {"d": dream})

    def lessons(self) -> list[dict[str, Any]]:
        return self.db.rows(
            "SELECT l.id, l.rule, l.target, l.status, l.created_at, r.rating, r.dream_short_id, s.ord AS shot_ord FROM dream_lesson l "
            "LEFT JOIN dream_review r ON r.id = l.review_id LEFT JOIN dream_shot s ON s.id = r.shot_id "
            "WHERE l.status <> 'RETIRED' ORDER BY l.status, l.created_at DESC")

    def update(self, lesson_id: uuid.UUID, rule: str | None, target: str | None, status: str | None) -> None:
        if target is not None and target not in TARGETS:
            raise ValueError(f"Target is one of {TARGETS}")
        if status is not None and status not in STATUSES:
            raise ValueError(f"Status is one of {STATUSES}")
        self.db.execute("UPDATE dream_lesson SET rule = COALESCE(%(rule)s, rule), target = COALESCE(%(t)s, target), "
                        "status = COALESCE(%(s)s, status), updated_at = now() WHERE id = %(id)s",
                        {"rule": _blank_to_none(rule), "t": target, "s": status, "id": lesson_id})

    def active(self, target: str) -> list[str]:
        return [r["rule"] for r in self.db.rows(
            "SELECT rule FROM dream_lesson WHERE status = 'ACTIVE' AND target IN ('ALL', %(t)s) ORDER BY created_at", {"t": target})]

    def for_prompt(self, target: str) -> str:
        rules = self.active(target)
        return "" if not rules else "\nThe owner's rules learned from earlier Shorts (always follow): - " + " - ".join(rules)
