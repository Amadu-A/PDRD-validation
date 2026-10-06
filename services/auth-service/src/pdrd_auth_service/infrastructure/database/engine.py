# services/auth-service/src/pdrd_auth_service/infrastructure/database/engine.py

"""Создаёт пул PostgreSQL для собственной схемы auth-service."""

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from pdrd_auth_service.core.settings import DatabaseSettings


def build_database_url(settings: DatabaseSettings) -> URL:
    """Собирает URL без конкатенации и печати секрета базы."""
    return URL.create(
        drivername="postgresql+asyncpg",
        username=settings.user,
        password=settings.password.get_secret_value(),
        host=settings.host,
        port=settings.port,
        database=settings.name,
    )


def build_async_engine(settings: DatabaseSettings) -> AsyncEngine:
    """Открывает соединения только по явному запросу сценария или миграции."""
    return create_async_engine(
        build_database_url(settings),
        pool_pre_ping=True,
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        pool_timeout=settings.pool_timeout_seconds,
        connect_args={"timeout": settings.connect_timeout_seconds},
    )


def build_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """Выделяет короткую SQLAlchemy-сессию на одну операцию хранилища."""
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
