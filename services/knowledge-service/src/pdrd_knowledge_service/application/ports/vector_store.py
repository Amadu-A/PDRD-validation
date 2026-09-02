# services/knowledge-service/src/pdrd_knowledge_service/application/ports/vector_store.py

"""Application port vector storage."""

from typing import Protocol

from pdrd_knowledge_service.domain.project_context import (
    VectorRecord,
)
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

    async def create_collection(
        self,
        *,
        collection: str,
        vector_size: int,
    ) -> None:
        """Создаёт Cosine vector collection."""
        ...

    async def upsert(
        self,
        *,
        collection: str,
        records: tuple[
            VectorRecord,
            ...,
        ],
    ) -> None:
        """Сохраняет vector records."""
        ...

    async def delete_by_filter(
        self,
        *,
        collection: str,
        key: str,
        value: str,
    ) -> None:
        """Удаляет points, payload которых соответствует фильтру."""
        ...

    async def delete_collection(
        self,
        *,
        collection: str,
    ) -> bool:
        """Идемпотентно удаляет collection."""
        ...

    async def is_ready(
        self,
    ) -> bool:
        """Проверяет доступность vector storage."""
        ...

    async def collection_exists(
        self,
        collection: str,
    ) -> bool:
        """Проверяет существование vector collection."""
        ...
