# services/api-gateway/src/pdrd_api_gateway/application/ports/document_context.py

"""Порт жизненного цикла временного контекста проверяемого PDF."""

from datetime import datetime
from typing import Protocol
from uuid import UUID


class DocumentContextLifecycle(Protocol):
    """Очистка задания и ограниченный просмотр старых кандидатов."""

    async def cleanup(self, *, context_id: UUID) -> None:
        """Удаляет индекс идемпотентно."""
        ...

    async def stale(
        self, *, before: datetime, limit: int, cursor: str
    ) -> tuple[tuple[UUID, ...], str]:
        """Возвращает кандидатов без удаления активных заданий."""
        ...
