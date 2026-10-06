# services/user-service/src/pdrd_user_service/infrastructure/database/engine.py

"""Подключение к PostgreSQL через общие типизированные настройки User Service."""

from pdrd_user_service.core.settings import DatabaseSettings
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def build_database_url(settings: DatabaseSettings) -> URL:
    """Строит URL без ручного соединения частей или логирования пароля."""
    return URL.create(
        drivername="postgresql+asyncpg",
        username=settings.user,
        password=settings.password.get_secret_value(),
        host=settings.host,
        port=settings.port,
        database=settings.name,
    )


def build_async_engine(settings: DatabaseSettings) -> AsyncEngine:
    """Создаёт пул соединений для собственного хранилища users."""
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
    """Создаёт независимую сессию на одну единицу работы."""
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
