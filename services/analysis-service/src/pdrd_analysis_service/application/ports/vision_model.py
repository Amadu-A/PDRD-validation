# services/analysis-service/src/pdrd_analysis_service/application/ports/vision_model.py

"""Application port structured vision model."""

from collections.abc import (
    AsyncIterator,
)
from contextlib import (
    AbstractAsyncContextManager,
    asynccontextmanager,
)
from typing import (
    Any,
    Protocol,
    runtime_checkable,
)

from pdrd_analysis_service.domain.analysis import (
    GenerationResult,
)


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

    async def is_ready(
        self,
    ) -> bool:
        """Проверяет наличие требуемой VLM-модели."""
        ...


@runtime_checkable
class ResidentStructuredVisionModel(Protocol):
    """Optional capability удержания VLM внутри bounded operation."""

    def residency_scope(
        self,
    ) -> AbstractAsyncContextManager[None]:
        """Возвращает bounded scope одного GPU lease/model residency."""
        ...


@asynccontextmanager
async def vision_model_residency(
    model: StructuredVisionModel,
) -> AsyncIterator[None]:
    """Удерживает model resident, если provider поддерживает capability.

    Test doubles и альтернативные providers без residency_scope
    продолжают работать в legacy режиме.
    """
    if isinstance(
        model,
        ResidentStructuredVisionModel,
    ):
        async with model.residency_scope():
            yield

        return

    yield
