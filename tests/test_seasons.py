import pytest

pytest.importorskip("sqlalchemy")
from datetime import datetime, timezone

from services import seasons as S


def test_periods():
    now = datetime(2026, 11, 18, 10, tzinfo=timezone.utc)          # a Wednesday
    assert S.week_start(now) == datetime(2026, 11, 16, tzinfo=timezone.utc)
    assert S.next_reset("week", now) == datetime(2026, 11, 23, tzinfo=timezone.utc)
    assert S.next_reset("month", now) == datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert S.month_id(now) == "2026-11" and S.week_id(now) == "2026-W47"
