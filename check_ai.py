"""Tests every configured AI provider/key WITHOUT starting the bot:  python check_ai.py"""
from __future__ import annotations

import asyncio
import logging

from ai.service import build_provider
from config import get_settings


async def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    st = get_settings()
    try:
        p = build_provider(st)
    except ValueError as e:
        raise SystemExit(str(e))
    print(f"{p.key_count} key(s) in total, providers: {p.names()}")
    ok, why = await p.self_test()
    await p.close()
    print(("OK   " if ok else "FAIL ") + why)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
