from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from sqlalchemy import event, insert
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from database.models import Base


class Database:
    def __init__(self) -> None:
        self.engine: AsyncEngine | None = None
        self._sm: async_sessionmaker[AsyncSession] | None = None

    def init(self, url: str, **kw: Any) -> None:
        if url.startswith("sqlite"):
            if ":memory:" not in url:                  # file DB: few connections = few threads = less RAM
                kw.setdefault("pool_size", 2)
                kw.setdefault("max_overflow", 3)
            self.engine = create_async_engine(url, connect_args={"timeout": 30}, **kw)

            @event.listens_for(self.engine.sync_engine, "connect")
            def _connect(dbapi_conn, _rec):  # take over transaction control (serialised writers)
                dbapi_conn.isolation_level = None
                try:                                    # ~2 MB page cache per connection instead of the default; no memory-mapped IO
                    dbapi_conn.execute("PRAGMA cache_size=-2000")
                    dbapi_conn.execute("PRAGMA mmap_size=0")
                except Exception:
                    pass

            @event.listens_for(self.engine.sync_engine, "begin")
            def _begin(conn):
                conn.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            self.engine = create_async_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=10, **kw)
        self._sm = async_sessionmaker(self.engine, expire_on_commit=False)

    @property
    def dialect(self) -> str:
        assert self.engine is not None
        return self.engine.dialect.name

    @asynccontextmanager
    async def tx(self) -> AsyncIterator[AsyncSession]:
        """One atomic transaction. Any exception rolls back EVERYTHING done inside."""
        assert self._sm is not None, "Database.init() not called"
        async with self._sm() as s:
            async with s.begin():
                yield s

    async def create_all(self) -> None:  # tests / first boot convenience (production uses Alembic)
        assert self.engine is not None
        async with self.engine.begin() as c:
            await c.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        if self.engine:
            await self.engine.dispose()


db = Database()


async def insert_ignore(s: AsyncSession, model: type, values: dict[str, Any]) -> bool:
    """INSERT .. ON CONFLICT DO NOTHING. True if a row was inserted (no exceptions, no aborted txn)."""
    if db.dialect == "postgresql":
        stmt = postgresql.insert(model).values(**values).on_conflict_do_nothing()
    elif db.dialect == "sqlite":
        stmt = sqlite.insert(model).values(**values).on_conflict_do_nothing()
    else:  # pragma: no cover
        stmt = insert(model).values(**values)
    res = await s.execute(stmt)
    return res.rowcount == 1
