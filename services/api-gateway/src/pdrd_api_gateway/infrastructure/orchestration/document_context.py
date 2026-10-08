# services/api-gateway/src/pdrd_api_gateway/infrastructure/orchestration/document_context.py

"""Закрытый HTTP-адаптер очистки временного D-индекса."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import httpx


@dataclass(frozen=True, slots=True)
class KnowledgeDocumentContextLifecycle:
    """Использует уже настроенный серверный ключ retention."""

    base_url: str
    internal_key: str

    async def cleanup(self, *, context_id: UUID) -> None:
        """Повторное удаление не влияет на сохранённый результат анализа."""
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.delete(
                f"{self.base_url.rstrip('/')}/internal/v1/document-contexts/{context_id}",
                headers={"X-PDRD-Retention-Key": self.internal_key},
            )
            response.raise_for_status()

    async def stale(
        self, *, before: datetime, limit: int, cursor: str
    ) -> tuple[tuple[UUID, ...], str]:
        """Получает ограниченный пакет идентификаторов без содержимого PDF."""
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{self.base_url.rstrip('/')}/internal/v1/document-contexts/stale",
                json={"before": before.isoformat(), "limit": limit, "cursor": cursor},
                headers={"X-PDRD-Retention-Key": self.internal_key},
            )
            response.raise_for_status()
            payload = response.json()
        return tuple(UUID(value) for value in payload["document_ids"]), str(
            payload["cursor"]
        )
