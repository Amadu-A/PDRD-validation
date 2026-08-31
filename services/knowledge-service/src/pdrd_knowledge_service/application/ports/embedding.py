# services/knowledge-service/src/pdrd_knowledge_service/application/ports/embedding.py

"""Application port embedding provider."""

from typing import Protocol


class EmbeddingProviderError(RuntimeError):
    """Ошибка внешнего embedding provider."""


class EmbeddingProvider(Protocol):
    """Контракт построения embeddings."""

    async def embed(
        self,
        texts: tuple[str, ...],
        *,
        instruction: str,
    ) -> list[list[float]]:
        """Строит embedding для каждого переданного текста."""
        ...

    async def is_ready(self) -> bool:
        """Проверяет доступность требуемой embedding-модели."""
        ...
