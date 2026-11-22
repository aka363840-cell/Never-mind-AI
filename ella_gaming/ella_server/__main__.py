"""python -m ella_server            -> run the server
python -m ella_server scan         -> fetch the catalogue, verify every game once, print the report, exit
python -m ella_server report       -> print the report from the cache/database without scanning"""
from __future__ import annotations

import asyncio
import json
import logging
import sys


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def _scan(force: bool, do_scan: bool) -> None:
    from .catalogue import Catalogue
    from .config import load
    from .db import Database
    from .scanner import Scanner
    st = load()
    db, cat = Database(st.db_path), Catalogue(st)
    cat.load_disk_cache()
    if do_scan or not cat.games:
        if not await cat.refresh(force=True):
            print("could not fetch the catalogue:", cat.last_error)
    sc = Scanner(st, cat, db)
    await sc.load_verdicts()
    if do_scan:
        await sc.scan_all(force=force)
    print(json.dumps(cat.report(), indent=2))
    if sc.browser_note:
        print("note:", sc.browser_note)
    db.close()


def main() -> None:
    _setup_logging()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd in ("scan", "report"):
        asyncio.run(_scan(force="--force" in sys.argv, do_scan=cmd == "scan"))
        return
    import os

    import uvicorn
    uvicorn.run("ella_server.app:create_app", factory=True, host=os.getenv("ELLA_HOST", "0.0.0.0"),
                port=int(os.getenv("PORT", os.getenv("ELLA_PORT", "8080"))), proxy_headers=False, log_level="info")


if __name__ == "__main__":
    main()
