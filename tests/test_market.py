import pytest

pytest.importorskip("sqlalchemy")
from services import market


def test_fee():
    assert market.net_after_fee(100) == 95
    assert market.net_after_fee(1) == 1          # tiny sales are fee-free (int rounding)
    assert market.net_after_fee(1000) + int(1000 * market.FEE) == 1000
