# services/experience-service/src/pdrd_experience_service/core/container.py

"""Composition Root микросервиса Experience Service.

Назначение файла:
- получить единую типизированную конфигурацию;
- создать инфраструктурные зависимости;
- подключить их к application use cases;
- управлять освобождением ресурсов при остановке приложения.

Пока Experience Service выключен, engine PostgreSQL
не создаётся и соединения не открываются.

При включении создаётся только инфраструктура проверки
готовности. Business HTTP endpoints подключим отдельно,
после реализации проверки доступа к заданиям.
"""

from collections.abc import (
    Awaitable,
    Callable,
)
from dataclasses import dataclass

from pdrd_experience_service.application.use_cases.check_readiness import (
    CheckReadiness,
)
from pdrd_experience_service.core.settings import (
    Settings,
    get_settings,
)
from pdrd_experience_service.infrastructure.database.engine import (
    build_async_engine,
)
from pdrd_experience_service.infrastructure.database.health import (
    DatabaseReadinessProbe,
)

ShutdownCallback = Callable[
    [],
    Awaitable[None],
]


class DisabledDatabaseProbe:
    """Запрещает положительный readiness выключенного сервиса."""

    async def is_ready(self) -> bool:
        """Выключенный сервис не обращается к PostgreSQL."""
        return False


@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    """Хранит зависимости и управляет их жизненным циклом."""

    settings: Settings
    check_readiness: CheckReadiness
    shutdown_callback: ShutdownCallback

    async def close(self) -> None:
        """Освобождает инфраструктурные ресурсы при остановке HTTP."""
        await self.shutdown_callback()


def build_container(
    settings: Settings | None = None,
) -> ApplicationContainer:
    """Собирает зависимости из явной конфигурации.

    Возможность передать Settings напрямую используется тестами,
    чтобы не читать приватный .env и не создавать соединения.
    """
    actual_settings = settings if settings is not None else get_settings()

    if not actual_settings.enabled:

        async def shutdown_disabled() -> None:
            """Для выключенного сервиса освобождать нечего."""

        return ApplicationContainer(
            settings=actual_settings,
            check_readiness=CheckReadiness(
                database=DisabledDatabaseProbe(),
            ),
            shutdown_callback=shutdown_disabled,
        )

    engine = build_async_engine(
        actual_settings.database,
    )

    readiness = DatabaseReadinessProbe(
        engine=engine,
        timeout_seconds=(actual_settings.database.connect_timeout_seconds),
    )

    async def shutdown_database() -> None:
        """Корректно закрывает пул соединений PostgreSQL."""
        await engine.dispose()

    return ApplicationContainer(
        settings=actual_settings,
        check_readiness=CheckReadiness(
            database=readiness,
        ),
        shutdown_callback=shutdown_database,
    )
