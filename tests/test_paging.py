"""Arrow-page helper. Run: pytest tests/test_paging.py"""
from utils import paging


def test_total_pages_and_clamp():
    assert [paging.total_pages(n, 10) for n in (0, 1, 10, 11, 20, 21)] == [1, 1, 1, 2, 2, 3]
    assert paging.clamp(-5, 25, 10) == 0 and paging.clamp(1, 25, 10) == 1 and paging.clamp(99, 25, 10) == 2
    assert paging.clamp(3, 0, 10) == 0                                    # empty list: always page 0


def test_nav_arrows():
    cb = lambda p: f"x:{p}"
    row = lambda page, pages: [(b.text, b.callback_data) for b in paging.nav(cb, page, pages)]
    assert row(0, 1) == []                                                # single page: no arrows at all
    assert row(0, 3) == [("▶️", "x:1")]
    assert row(1, 3) == [("◀️", "x:0"), ("▶️", "x:2")]
    assert row(2, 3) == [("◀️", "x:1")]
    assert paging.footer(1, 3) == "\n<i>Page 2/3</i>" and paging.footer(0, 1) == ""
