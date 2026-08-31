# services/api-gateway/src/pdrd_api_gateway/core/container.py

"""Composition root микросервиса API Gateway."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pdrd_api_gateway.application.use_cases.check_readiness import (
    CheckReadiness,
)
from pdrd_api_gateway.core.settings import Settings, get_settings
from pdrd_api_gateway.infrastructure.database.engine import (
    build_async_engine,
)
from pdrd_api_gateway.infrastructure.database.health import (
    DatabaseReadinessProbe,
)

ShutdownCallback = Callable[
    [],
    Awaitable[None],
]


@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    """Хранит runtime dependencies API Gateway."""

    settings: Settings
    check_readiness: CheckReadiness
    shutdown_callback: ShutdownCallback

    async def close(self) -> None:
        """Корректно освобождает infrastructure resources."""
        await self.shutdown_callback()


def build_container() -> ApplicationContainer:
    """Собирает production dependencies API Gateway."""
    settings = get_settings()

    engine = build_async_engine(
        settings.database,
    )

    database_readiness = DatabaseReadinessProbe(
        engine=engine,
        timeout_seconds=(settings.database.health_timeout_seconds),
    )

    check_readiness = CheckReadiness(
        database=database_readiness,
    )

    async def _shutdown_database() -> None:
        await engine.dispose()

    return ApplicationContainer(
        settings=settings,
        check_readiness=check_readiness,
        shutdown_callback=_shutdown_database,
    )
