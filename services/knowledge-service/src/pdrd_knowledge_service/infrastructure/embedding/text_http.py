# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/embedding/text_http.py

"""Text-only adapter resident shared multimodal embedding."""

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
    """Адаптирует shared multimodal vLLM к text EmbeddingProvider."""

    def __init__(
        self,
        *,
        base_url: str,
        request_timeout_seconds: float,
        connect_timeout_seconds: float,
        health_timeout_seconds: float,
    ) -> None:
        """Создаёт bounded shared-vLLM delegate."""
        self._delegate = HttpMultimodalEmbeddingProvider(
            base_url=base_url,
            request_timeout_seconds=(request_timeout_seconds),
            connect_timeout_seconds=(connect_timeout_seconds),
            health_timeout_seconds=(health_timeout_seconds),
        )

    async def embed(
        self,
        texts: tuple[
            str,
            ...,
        ],
        *,
        instruction: str | None,
    ) -> list[list[float]]:
        """Строит batch text embeddings без model lifecycle."""
        if not texts:
            return []

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
            raise EmbeddingProviderError(
                str(
                    error,
                )
            ) from error

    async def is_ready(
        self,
    ) -> bool:
        """Проверяет resident shared embedding provider."""
        return await self._delegate.is_ready()
