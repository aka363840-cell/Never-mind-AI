"""Tiny helpers for "◀️ ▶️" pages. Pure functions - the callers own the queries and the callback handlers.

Pages are shared: in a group, whoever taps an arrow turns the page for everybody (by design).
"""
from __future__ import annotations

from typing import Callable

from aiogram.types import InlineKeyboardButton as B


def total_pages(total: int, per: int) -> int:
    return max(1, -(-int(total) // int(per)))


def clamp(page: int, total: int, per: int) -> int:
    """Keep a (possibly stale) page number inside 0 .. last page, e.g. when players were removed after the message was sent."""
    return min(max(0, int(page)), total_pages(total, per) - 1)


def nav(callback: Callable[[int], str], page: int, pages: int) -> list[B]:
    """One row of arrows: ◀️ when there is a previous page, ▶️ when there is a next one (empty list for a single page)."""
    row = []
    if page > 0:
        row.append(B(text="◀️", callback_data=callback(page - 1)))
    if page + 1 < pages:
        row.append(B(text="▶️", callback_data=callback(page + 1)))
    return row


def footer(page: int, pages: int) -> str:
    return f"\n<i>Page {page + 1}/{pages}</i>" if pages > 1 else ""
