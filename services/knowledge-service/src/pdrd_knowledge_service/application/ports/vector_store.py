# services/knowledge-service/src/pdrd_knowledge_service/application/ports/vector_store.py

"""Application port vector storage."""

from dataclasses import dataclass
from typing import (
    Any,
    Protocol,
)

from pdrd_knowledge_service.domain.project_context import (
    VectorRecord,
)
from pdrd_knowledge_service.domain.search import (
    VectorPoint,
    VectorSearchFilter,
)


class VectorStoreError(RuntimeError):
    """Ошибка внешнего vector storage."""


@dataclass(frozen=True, slots=True)
class StoredVectorPayload:
    """Persisted point metadata без vector."""

    point_id: str

    payload: dict[str, Any]


class VectorStore(Protocol):
    """Контракт vector storage."""

    async def search(
        self,
        *,
        collection: str,
        vector: list[float],
        limit: int,
    ) -> list[VectorPoint]:
        """Ищет ближайшие точки."""
        ...

    async def search_filtered(
        self,
        *,
        collection: str,
        vector: list[float],
        limit: int,
        search_filter: VectorSearchFilter,
    ) -> list[VectorPoint]:
        """Ищет points внутри payload scope."""
        ...

    async def create_collection(
        self,
        *,
        collection: str,
        vector_size: int,
    ) -> None:
        """Создаёт Cosine collection."""
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

    async def set_payload_by_filter(
        self,
        *,
        collection: str,
        key: str,
        value: str,
        payload: dict[str, Any],
    ) -> None:
        """Изменяет payload."""
        ...

    async def delete_by_filter(
        self,
        *,
        collection: str,
        key: str,
        value: str,
    ) -> None:
        """Удаляет filtered points."""
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
        """Проверяет Qdrant readiness."""
        ...

    async def collection_exists(
        self,
        collection: str,
    ) -> bool:
        """Проверяет collection/alias."""
        ...

    async def list_collections(
        self,
    ) -> tuple[str, ...]:
        """Возвращает physical collections."""
        ...

    async def get_alias_target(
        self,
        alias: str,
    ) -> str | None:
        """Возвращает physical target alias."""
        ...

    async def replace_aliases(
        self,
        aliases_to_targets: dict[str, str],
    ) -> None:
        """Atomically переключает aliases."""
        ...

    async def scroll_payloads(
        self,
        *,
        collection: str,
        batch_size: int = 256,
    ) -> tuple[StoredVectorPayload, ...]:
        """Читает все payloads collection без vectors."""
        ...
