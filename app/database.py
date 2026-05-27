import os
from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings
from app.models import Base

_settings = get_settings()
engine = create_async_engine(_settings.database_url, echo=False)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

# WAL mode lets the API (reader) and poller (writer) operate concurrently without
# "database is locked" errors — the default DELETE journal blocks reads during writes.
if "///:memory:" not in _settings.database_url:
    @event.listens_for(engine.sync_engine, "connect")
    def _set_wal_mode(dbapi_conn, _record):  # type: ignore[misc]
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


async def init_db() -> None:
    db_url = _settings.database_url
    if "///:memory:" not in db_url:
        db_path = db_url.split("///", 1)[-1]
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_db() -> None:
    await engine.dispose()


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session
