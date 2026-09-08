# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/embedding/text_http.py

"""Text-only adapter unified multimodal embedding service."""

from pdrd_knowledge_service.application.ports.embedding import (
    EmbeddingProviderError,
)
from pdrd_knowledge_service.application.ports.multimodal_embedding import (
    MultimodalEmbeddingInput,
    MultimodalEmbeddingProviderError,
)
from pdrd_knowledge_service.infrastructure.embedding.multimodal_http import (
    HttpMultimodalEmbeddingProvider,
)


class HttpTextEmbeddingProvider:
    """Адаптирует unified multimodal service к text EmbeddingProvider."""

    def __init__(
        self,
        *,
        base_url: str,
        request_timeout_seconds: float,
        connect_timeout_seconds: float,
        health_timeout_seconds: float,
    ) -> None:
        """Создаёт bounded delegate."""
        self._delegate = HttpMultimodalEmbeddingProvider(
            base_url=base_url,
            request_timeout_seconds=(request_timeout_seconds),
            connect_timeout_seconds=(connect_timeout_seconds),
            health_timeout_seconds=(health_timeout_seconds),
        )

    async def embed(
        self,
        texts: tuple[str, ...],
        *,
        instruction: str | None,
    ) -> list[list[float]]:
        """Строит text embeddings и всегда освобождает checkpoint."""
        if not texts:
            return []

        primary_error: Exception | None = None

        try:
            return await self._delegate.embed(
                tuple(
                    MultimodalEmbeddingInput(
                        text=text,
                        instruction=instruction,
                    )
                    for text in texts
                )
            )

        except MultimodalEmbeddingProviderError as error:
            primary_error = error

            raise EmbeddingProviderError(
                str(
                    error,
                )
            ) from error

        finally:
            try:
                await self._delegate.release()

            except MultimodalEmbeddingProviderError as error:
                if primary_error is None:
                    raise EmbeddingProviderError(
                        "Не удалось освободить unified embedding checkpoint.",
                    ) from error

    async def is_ready(
        self,
    ) -> bool:
        """Проверяет unified provider."""
        return await self._delegate.is_ready()
