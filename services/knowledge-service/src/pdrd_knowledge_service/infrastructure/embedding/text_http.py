# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/embedding/text_http.py

"""Text-only adapter resident shared multimodal embedding."""

from pdrd_knowledge_service.application.ports.embedding import (
    EmbeddingProviderError,
    EmbeddingTemporaryError,
)
from pdrd_knowledge_service.application.ports.multimodal_embedding import (
    MultimodalEmbeddingInput,
    MultimodalEmbeddingProviderError,
    MultimodalEmbeddingTemporaryError,
)
from pdrd_knowledge_service.infrastructure.embedding.multimodal_http import (
    HttpMultimodalEmbeddingProvider,
)
from pdrd_knowledge_service.infrastructure.embedding.request_runtime import (
    EmbeddingRequestRuntime,
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
        output_dimension: int = 4096,
        runtime: EmbeddingRequestRuntime | None = None,
    ) -> None:
        """Создаёт bounded shared-vLLM delegate."""
        options = {"runtime": runtime} if runtime is not None else {}
        self._delegate = HttpMultimodalEmbeddingProvider(
            base_url=base_url,
            request_timeout_seconds=(request_timeout_seconds),
            connect_timeout_seconds=(connect_timeout_seconds),
            health_timeout_seconds=(health_timeout_seconds),
            output_dimension=output_dimension,
            **options,
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

        except MultimodalEmbeddingTemporaryError as error:
            raise EmbeddingTemporaryError(
                str(error),
                reason_code=error.reason_code,
                status_code=error.status_code,
                retry_after=error.retry_after,
            ) from error
        except MultimodalEmbeddingProviderError as error:
            raise EmbeddingProviderError(
                str(error),
                reason_code=error.reason_code,
                status_code=error.status_code,
            ) from error

    async def is_ready(
        self,
    ) -> bool:
        """Проверяет resident shared embedding provider."""
        return await self._delegate.is_ready()
