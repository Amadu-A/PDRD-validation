# services/multimodal-embedding-service/src/pdrd_multimodal_embedding_service/schemas.py

"""HTTP schemas multimodal embedding service."""

from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


class EmbeddingInputPayload(BaseModel):
    """Один text/image/mixed embedding input."""

    model_config = ConfigDict(
        extra="forbid",
    )

    text: str | None = Field(
        default=None,
        max_length=50_000,
    )

    image_base64: str | None = None

    instruction: str | None = Field(
        default=None,
        max_length=2000,
    )

    @model_validator(
        mode="after",
    )
    def validate_modalities(
        self,
    ) -> Self:
        """Требует хотя бы text или image."""
        has_text = self.text is not None and bool(
            self.text.strip(),
        )

        has_image = self.image_base64 is not None and bool(
            self.image_base64.strip(),
        )

        if not has_text and not has_image:
            raise ValueError(
                "Embedding input требует text и/или image.",
            )

        return self


class EmbeddingRequest(BaseModel):
    """Запрос bounded multimodal embedding."""

    model_config = ConfigDict(
        extra="forbid",
    )

    inputs: list[EmbeddingInputPayload] = Field(
        min_length=1,
        max_length=1,
    )


class EmbeddingResponse(BaseModel):
    """Ответ embedding provider."""

    embeddings: list[list[float]]

    model: str

    dimension: int


class ModelStatusResponse(BaseModel):
    """Текущее состояние GPU/model runtime."""

    model: str

    model_loaded: bool

    cuda_available: bool

    free_vram_bytes: int | None

    total_vram_bytes: int | None


class HealthResponse(BaseModel):
    """Liveness/readiness response."""

    status: str

    service: str

    version: str
