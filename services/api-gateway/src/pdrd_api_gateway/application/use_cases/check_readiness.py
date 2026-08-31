# services/api-gateway/src/pdrd_api_gateway/application/use_cases/check_readiness.py

"""Use case проверки готовности API Gateway."""

from dataclasses import dataclass

from pdrd_api_gateway.application.ports.readiness import (
    DatabaseReadinessPort,
)


@dataclass(frozen=True, slots=True)
class ReadinessStatus:
    """Результат проверки обязательных infrastructure dependencies."""

    database_ready: bool

    @property
    def ready(self) -> bool:
        """Возвращает общую готовность приложения."""
        return self.database_ready


@dataclass(frozen=True, slots=True)
class CheckReadiness:
    """Проверяет обязательные инфраструктурные зависимости."""

    database: DatabaseReadinessPort

    async def execute(self) -> ReadinessStatus:
        """Выполняет readiness-проверку."""
        return ReadinessStatus(
            database_ready=await self.database.is_ready(),
        )
