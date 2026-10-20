# services/knowledge-service/src/pdrd_knowledge_service/application/ports/embedding.py

"""Application port embedding provider."""

from typing import Protocol


class EmbeddingProviderError(RuntimeError):
    """Ошибка внешнего embedding provider."""

    def __init__(
        self,
        message: str,
        *,
        reason_code: str = "embedding_invalid_response",
        status_code: int | None = None,
    ) -> None:
        """Хранит постоянную причину отдельно от временной перегрузки и сетевого timeout."""
        super().__init__(message)
        self.reason_code = reason_code
        self.status_code = status_code


class EmbeddingTemporaryError(EmbeddingProviderError):
    """Временный отказ с причиной и подсказкой ожидания, без повторного запуска конвейера."""

    def __init__(
        self,
        message: str,
        *,
        reason_code: str = "provider_unavailable",
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Сохраняет машинную классификацию без текста документа и векторов."""
        super().__init__(message)
        self.reason_code, self.status_code, self.retry_after = (
            reason_code,
            status_code,
            retry_after,
        )


class EmbeddingProvider(Protocol):
    """Контракт построения embeddings."""

    async def embed(
        self,
        texts: tuple[str, ...],
        *,
        instruction: str | None,
    ) -> list[list[float]]:
        """Строит embedding для каждого текста."""
        ...

    async def is_ready(
        self,
    ) -> bool:
        """Проверяет доступность embedding-модели."""
        ...
