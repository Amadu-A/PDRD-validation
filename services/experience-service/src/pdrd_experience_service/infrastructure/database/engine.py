# services/experience-service/src/pdrd_experience_service/infrastructure/database/engine.py

"""Фабрики подключения к PostgreSQL для Experience Service.

Назначение файла:
- безопасно формировать PostgreSQL DSN;
- создавать асинхронный SQLAlchemy engine;
- настраивать ограниченный пул соединений;
- предоставлять фабрику независимых AsyncSession.

Фабрики получают настройки через Dependency Injection.

Модуль не запускает миграции и не устанавливает соединения
при импорте. Жизненным циклом engine должен управлять
будущий composition root Experience Service.
"""

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from pdrd_experience_service.core.settings import (
    DatabaseSettings,
)


def build_database_url(
    settings: DatabaseSettings,
) -> URL:
    """Формирует SQLAlchemy URL без ручной конкатенации.

    URL.create корректно обрабатывает специальные символы
    в имени пользователя, пароле и других параметрах.
    """
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
    """Создаёт асинхронный engine с ограниченным пулом.

    Pool pre-ping помогает обнаруживать соединения,
    которые PostgreSQL уже закрыл.

    Сам вызов create_async_engine не выполняет миграции
    и не должен создавать реальное сетевое подключение.
    """
    return create_async_engine(
        build_database_url(settings),
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
    """Создаёт фабрику независимых сессий для ReviewRepository.

    Каждая операция репозитория получает собственную AsyncSession.
    Это сохраняет существующую модель транзакций и позволяет
    выполнять проверку конкурентных изменений в PostgreSQL.

    Фиксацией транзакций продолжает управлять репозиторий.
    """
    return async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        autoflush=False,
    )
