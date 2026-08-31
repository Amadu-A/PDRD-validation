# services/api-gateway/src/pdrd_api_gateway/application/ports/readiness.py

"""Абстракции проверки готовности infrastructure dependencies."""

from typing import Protocol


class DatabaseReadinessPort(Protocol):
    """Контракт проверки доступности application database."""

    async def is_ready(self) -> bool:
        """Возвращает доступность базы данных."""
        ...


class BrokerReadinessPort(Protocol):
    """Контракт проверки доступности message broker."""

    async def is_ready(self) -> bool:
        """Возвращает доступность message broker."""
        ...
