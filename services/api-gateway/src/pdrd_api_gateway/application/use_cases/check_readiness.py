# services/api-gateway/src/pdrd_api_gateway/application/use_cases/check_readiness.py

"""Use case проверки готовности API Gateway."""

import asyncio
from dataclasses import dataclass

from pdrd_api_gateway.application.ports.readiness import (
    BrokerReadinessPort,
    DatabaseReadinessPort,
)


@dataclass(frozen=True, slots=True)
class ReadinessStatus:
    """Результат проверки обязательных infrastructure dependencies."""

    database_ready: bool
    broker_ready: bool

    @property
    def ready(self) -> bool:
        """Возвращает общую готовность приложения."""
        return self.database_ready and self.broker_ready


@dataclass(frozen=True, slots=True)
class CheckReadiness:
    """Проверяет обязательные инфраструктурные зависимости."""

    database: DatabaseReadinessPort
    broker: BrokerReadinessPort

    async def execute(self) -> ReadinessStatus:
        """Параллельно выполняет все readiness checks."""
        database_ready, broker_ready = await asyncio.gather(
            self.database.is_ready(),
            self.broker.is_ready(),
        )

        return ReadinessStatus(
            database_ready=database_ready,
            broker_ready=broker_ready,
        )
