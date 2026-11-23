"""In-memory stand-in for the database, recording statements; answers canned rows per query keyword."""

from contextlib import contextmanager


class FakeDb:
    def __init__(self, answers=None):
        self.answers = answers or {}
        self.executed = []
        self.queries = []
        self.transactions = 0

    def rows(self, sql, params=None):
        self.queries.append((sql, params))
        for key, rows in self.answers.items():
            if key in sql:
                return rows
        return []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    @contextmanager
    def transaction(self):
        self.transactions += 1
        yield
