# services/knowledge-service/src/pdrd_knowledge_service/application/ports/readiness.py

"""Абстракции readiness infrastructure dependencies Knowledge Service."""

from typing import Protocol


class DatabaseReadinessPort(Protocol):
    """Контракт готовности PostgreSQL нормативного каталога."""

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает готовность database schema к работе."""
        ...
