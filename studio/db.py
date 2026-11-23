"""Small database helper. Money code depends on the `Db` interface only, so tests can swap in a fake or a rolled-back connection."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Protocol


class Db(Protocol):
    def rows(self, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]: ...

    def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> None: ...

    def transaction(self) -> Any:
        """Context manager: statements inside commit together or not at all."""
        ...


class PgDb:
    """psycopg 3 connection (imported lazily: the library lives in the `studio` dependency group)."""

    def __init__(self, dsn: str, conn: Any | None = None):
        import psycopg
        from psycopg.rows import dict_row

        self._conn = conn or psycopg.connect(dsn, row_factory=dict_row, autocommit=True)

    def rows(self, sql, params=None):
        with self._conn.cursor() as cur:
            cur.execute(sql, params or {})
            return list(cur.fetchall())

    def execute(self, sql, params=None):
        with self._conn.cursor() as cur:
            cur.execute(sql, params or {})

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._conn.transaction():
            yield

    def close(self) -> None:
        self._conn.close()
