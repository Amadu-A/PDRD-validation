# services/analysis-service/src/pdrd_analysis_service/application/ports/vision_model.py

"""Application port structured vision model."""

from typing import (
    Any,
    Protocol,
)

from pdrd_analysis_service.domain.analysis import (
    GenerationMetrics,
    GenerationResult,
)


class VisionModelError(RuntimeError):
    """Ошибка structured VLM provider."""

    def __init__(
        self,
        message: str,
        *,
        metrics: GenerationMetrics | None = None,
        status_code: int | None = None,
        error_code: str = "",
        retry_after_seconds: float = 0,
    ) -> None:
        """Сохраняет известные затраты, не сохраняя исходный ответ провайдера."""
        super().__init__(message)
        self.metrics = metrics
        self.status_code = status_code
        self.error_code = error_code
        self.retry_after_seconds = retry_after_seconds
        self.retryable = status_code in {
            408,
            425,
            429,
            500,
            502,
            503,
            504,
        } and error_code not in {
            "context_length_exceeded",
            "invalid_request_error",
            "invalid_request",
        }


class VisionModelOutputBudgetError(VisionModelError):
    """Полный ответ не поместился; повтор требует изменения размера задания."""

    def __init__(
        self, message: str, *, metrics: GenerationMetrics | None = None
    ) -> None:
        """Сохраняет метрики исчерпанной попытки без публикации обрезанного JSON."""
        super().__init__(message)
        self.metrics = metrics


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

    async def is_ready(
        self,
    ) -> bool:
        """Проверяет наличие требуемой VLM-модели."""
        ...
