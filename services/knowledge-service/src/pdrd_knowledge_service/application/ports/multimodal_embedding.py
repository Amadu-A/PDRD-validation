# services/knowledge-service/src/pdrd_knowledge_service/application/ports/multimodal_embedding.py

"""Application port multimodal embedding provider."""

from dataclasses import dataclass
from typing import Protocol

from pdrd_knowledge_service.application.ports.embedding import (
    EmbeddingProviderError,
    EmbeddingTemporaryError,
)


class MultimodalEmbeddingProviderError(
    EmbeddingProviderError,
):
    """Ошибка multimodal embedding provider."""


class MultimodalEmbeddingTemporaryError(
    MultimodalEmbeddingProviderError,
    EmbeddingTemporaryError,
):
    """Временная GPU/provider ошибка, допускающая retry."""

    def __init__(
        self,
        message: str,
        *,
        reason_code: str = "provider_unavailable",
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Передаёт типизированную временную причину через text-only адаптер."""
        EmbeddingTemporaryError.__init__(
            self,
            message,
            reason_code=reason_code,
            status_code=status_code,
            retry_after=retry_after,
        )


@dataclass(frozen=True, slots=True)
class MultimodalEmbeddingInput:
    """Один text/image/mixed input для embedding-модели."""

    text: str | None = None

    image_bytes: bytes | None = None

    instruction: str | None = None

    def __post_init__(
        self,
    ) -> None:
        """Проверяет наличие хотя бы одной modality."""
        has_text = isinstance(
            self.text,
            str,
        ) and bool(
            self.text.strip(),
        )

        has_image = isinstance(
            self.image_bytes,
            bytes,
        ) and bool(
            self.image_bytes,
        )

        if not has_text and not has_image:
            raise ValueError(
                "Multimodal embedding input требует text и/или image_bytes.",
            )

        if (
            isinstance(
                self.text,
                str,
            )
            and "\x00" in self.text
        ):
            raise ValueError(
                "Multimodal text содержит NUL-символ.",
            )

        if (
            isinstance(
                self.instruction,
                str,
            )
            and "\x00" in self.instruction
        ):
            raise ValueError(
                "Multimodal instruction содержит NUL-символ.",
            )


class MultimodalEmbeddingProvider(
    Protocol,
):
    """Контракт Qwen3-VL-Embedding-compatible provider."""

    async def embed(
        self,
        inputs: tuple[
            MultimodalEmbeddingInput,
            ...,
        ],
    ) -> list[list[float]]:
        """Строит embedding для каждого multimodal input."""
        ...

    async def release(
        self,
    ) -> None:
        """Просит provider явно выгрузить checkpoint."""
        ...

    async def is_ready(
        self,
    ) -> bool:
        """Проверяет готовность provider."""
        ...
