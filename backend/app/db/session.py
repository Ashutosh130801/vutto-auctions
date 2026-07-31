"""Async engine / session management.

One engine per process.  ``get_session`` is the FastAPI dependency: it opens a
transaction-scoped session, commits on success and rolls back on any exception,
so handlers never have to remember to do either.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def create_engine(url: str | None = None) -> AsyncEngine:
    return create_async_engine(
        url or settings.async_database_url,
        echo=settings.database_echo,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_pre_ping=True,  # survive DB restarts / idle connection reaping
        pool_recycle=1800,
        future=True,
    )


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_engine()
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(), expire_on_commit=False, autoflush=False
        )
    return _sessionmaker


def configure(engine: AsyncEngine) -> None:
    """Test hook: point the process at an externally managed engine."""
    global _engine, _sessionmaker
    _engine = engine
    _sessionmaker = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine, _sessionmaker = None, None


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency — unit of work per request."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Same contract as ``get_session`` for use outside the request cycle."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
