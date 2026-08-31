# services/knowledge-service/src/pdrd_knowledge_service/application/ports/vector_store.py

"""Application port vector storage."""

from typing import Protocol

from pdrd_knowledge_service.domain.search import VectorPoint


class VectorStoreError(RuntimeError):
    """Ошибка внешнего vector storage."""


class VectorStore(Protocol):
    """Контракт vector search storage."""

    async def search(
        self,
        *,
        collection: str,
        vector: list[float],
        limit: int,
    ) -> list[VectorPoint]:
        """Ищет ближайшие точки в одной коллекции."""
        ...

    async def is_ready(self) -> bool:
        """Проверяет доступность vector storage."""
        ...

    async def collection_exists(
        self,
        collection: str,
    ) -> bool:
        """Проверяет существование vector collection."""
        ...
