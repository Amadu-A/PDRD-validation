# services/analysis-service/src/pdrd_analysis_service/application/ports/vision_model.py

"""Application port structured vision model."""

from typing import Any, Protocol

from pdrd_analysis_service.domain.analysis import GenerationResult


class VisionModelError(RuntimeError):
    """Ошибка structured VLM provider."""


class StructuredVisionModel(Protocol):
    """Контракт structured VLM."""

    async def generate_json(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        num_predict: int,
        seed: int,
        stage: str,
        image_bytes: bytes | None = None,
    ) -> GenerationResult:
        """Генерирует JSON по переданной схеме."""
        ...

    async def is_ready(self) -> bool:
        """Проверяет наличие требуемой VLM-модели."""
        ...
