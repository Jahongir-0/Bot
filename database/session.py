from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from config.settings import get_settings


class Base(DeclarativeBase):
    pass


def create_engine_from_url(database_url: Optional[str] = None) -> AsyncEngine:
    settings = get_settings()
    url = database_url or settings.database_url
    is_sqlite = url.startswith("sqlite")

    connect_args = {"check_same_thread": False} if is_sqlite else {}
    engine_kwargs = {
        "echo": False,
        "future": True,
        "pool_pre_ping": True,
        "connect_args": connect_args,
    }
    if not is_sqlite:
        engine_kwargs["pool_size"] = 15
        engine_kwargs["max_overflow"] = 25

    eng = create_async_engine(url, **engine_kwargs)

    if is_sqlite:
        @event.listens_for(eng.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON;")
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.close()

    return eng


engine: AsyncEngine = create_engine_from_url()
AsyncSessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionFactory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def init_db(target_engine: Optional[AsyncEngine] = None) -> None:
    import models  # noqa: F401 - ensures all ORM models are registered on Base.metadata

    eng = target_engine or engine
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db(target_engine: Optional[AsyncEngine] = None) -> None:
    eng = target_engine or engine
    await eng.dispose()
