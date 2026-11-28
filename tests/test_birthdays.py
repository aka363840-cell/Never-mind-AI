import pytest

pytest.importorskip("sqlalchemy")
from services import birthdays as B
from services.errors import GameError


def test_parse():
    assert B.parse_md("25-12") == "12-25"
    assert B.parse_md("5/3") == "03-05"
    assert B.parse_md("29 02") == "02-29"
    for bad in ("31-02", "0-1", "abc", "12-13", ""):
        with pytest.raises(GameError):
            B.parse_md(bad)
    assert B.pretty("12-25") == "25 Dec"
