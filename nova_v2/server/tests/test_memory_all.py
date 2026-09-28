"""memory.all() pages past PostgREST's row cap."""
import pytest

from app.store import memory


class FakeQuery:
    """Just enough of the supabase-py query builder for all()."""

    def __init__(self, rows, calls, cap):
        self._rows, self._calls, self._cap = rows, calls, cap
        self._start = self._end = None

    def select(self, *_):
        return self

    def order(self, *_, **__):
        return self

    def eq(self, column, value):
        self._calls.append(("eq", column, value))
        return self

    def range(self, start, end):
        self._start, self._end = start, end
        return self

    def execute(self):
        self._calls.append(("page", self._start, self._end))
        # PostgREST returns at most `cap` rows whatever range is asked for.
        end = min(self._end, self._start + self._cap - 1)
        self.data = self._rows[self._start:end + 1]
        return self


class FakeClient:
    def __init__(self, rows, cap):
        self.rows, self.cap, self.calls = rows, cap, []

    def table(self, _):
        return FakeQuery(self.rows, self.calls, self.cap)


@pytest.mark.parametrize("n", [0, 1, 999, 1000, 1001, 2500])
def test_returns_every_row_in_order(monkeypatch, n):
    rows = [{"id": i} for i in range(n)]
    client = FakeClient(rows, cap=1000)
    monkeypatch.setattr(memory, "get_client", lambda: client)

    assert memory.all("u1") == rows
    pages = [c for c in client.calls if c[0] == "page"]
    # One request per full page, plus the short (or empty) one that ends it.
    assert len(pages) == n // 1000 + 1
    # ...and every one of them is narrowed to the caller's rows.
    assert [c for c in client.calls if c[0] == "eq"] == [("eq", "user_id", "u1")] * len(pages)
