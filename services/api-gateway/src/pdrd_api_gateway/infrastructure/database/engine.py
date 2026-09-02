# services/api-gateway/src/pdrd_api_gateway/infrastructure/database/engine.py

"""Создание async SQLAlchemy engine и session factory."""

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from pdrd_api_gateway.core.settings import DatabaseSettings


def build_database_url(
    settings: DatabaseSettings,
) -> URL:
    """Создаёт SQLAlchemy URL без ручной конкатенации credentials."""
    return URL.create(
        drivername="postgresql+asyncpg",
        username=settings.user,
        password=settings.password.get_secret_value(),
        host=settings.host,
        port=settings.port,
        database=settings.name,
    )


def build_async_engine(
    settings: DatabaseSettings,
) -> AsyncEngine:
    """Создаёт async SQLAlchemy engine для PostgreSQL."""
    return create_async_engine(
        build_database_url(
            settings,
        ),
        pool_pre_ping=True,
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        pool_timeout=settings.pool_timeout_seconds,
        connect_args={
            "timeout": settings.connect_timeout_seconds,
        },
    )


def build_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """Создаёт фабрику независимых async database sessions."""
    return async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        autoflush=False,
    )
