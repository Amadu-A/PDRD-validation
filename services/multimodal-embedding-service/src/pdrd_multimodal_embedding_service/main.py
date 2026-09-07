# services/multimodal-embedding-service/src/pdrd_multimodal_embedding_service/main.py

"""FastAPI entry point multimodal embedding service."""

import base64
import binascii

from fastapi import (
    FastAPI,
    HTTPException,
    status,
)

from pdrd_multimodal_embedding_service.runtime import (
    GpuAdmissionError,
    MultimodalModelExecutionError,
    MultimodalRuntimeError,
    Qwen3VlEmbeddingRuntime,
    RuntimeEmbeddingInput,
)
from pdrd_multimodal_embedding_service.schemas import (
    EmbeddingRequest,
    EmbeddingResponse,
    HealthResponse,
    ModelStatusResponse,
)
from pdrd_multimodal_embedding_service.settings import (
    Settings,
    get_settings,
)


def _decode_image(
    value: str | None,
    *,
    max_image_bytes: int,
) -> bytes | None:
    """Декодирует bounded Base64 image."""
    if value is None:
        return None

    try:
        content = base64.b64decode(
            value,
            validate=True,
        )

    except (
        binascii.Error,
        ValueError,
    ) as error:
        raise HTTPException(
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail="image_base64 содержит некорректный Base64.",
        ) from error

    if not content:
        raise HTTPException(
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail="Передано пустое изображение.",
        )

    if (
        len(
            content,
        )
        > max_image_bytes
    ):
        raise HTTPException(
            status_code=(status.HTTP_413_CONTENT_TOO_LARGE),
            detail="Изображение превышает допустимый размер.",
        )

    return content


def create_app(
    *,
    settings: Settings | None = None,
    runtime: Qwen3VlEmbeddingRuntime | None = None,
) -> FastAPI:
    """Создаёт configured FastAPI application."""
    resolved_settings = settings if settings is not None else get_settings()

    resolved_runtime = (
        runtime
        if runtime is not None
        else Qwen3VlEmbeddingRuntime(
            settings=resolved_settings.model,
        )
    )

    application = FastAPI(
        title=resolved_settings.service_name,
        version=resolved_settings.service_version,
        docs_url=("/docs" if resolved_settings.docs_enabled else None),
        redoc_url=("/redoc" if resolved_settings.docs_enabled else None),
        openapi_url=("/openapi.json" if resolved_settings.docs_enabled else None),
    )

    @application.get(
        "/health/live",
        response_model=HealthResponse,
    )
    async def health_live() -> HealthResponse:
        """Возвращает liveness без загрузки checkpoint."""
        return HealthResponse(
            status="ok",
            service=resolved_settings.service_name,
            version=resolved_settings.service_version,
        )

    @application.get(
        "/health/ready",
        response_model=HealthResponse,
    )
    async def health_ready() -> HealthResponse:
        """Проверяет доступность CUDA без model load."""
        runtime_status = await resolved_runtime.status()

        if not runtime_status.cuda_available:
            raise HTTPException(
                status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
                detail="CUDA недоступна.",
            )

        return HealthResponse(
            status="ready",
            service=resolved_settings.service_name,
            version=resolved_settings.service_version,
        )

    @application.get(
        "/internal/v1/status",
        response_model=ModelStatusResponse,
    )
    async def model_status() -> ModelStatusResponse:
        """Возвращает VRAM/model status."""
        runtime_status = await resolved_runtime.status()

        return ModelStatusResponse(
            model=resolved_settings.model.name,
            model_loaded=runtime_status.model_loaded,
            cuda_available=runtime_status.cuda_available,
            free_vram_bytes=(runtime_status.free_vram_bytes),
            total_vram_bytes=(runtime_status.total_vram_bytes),
        )

    @application.post(
        "/internal/v1/embeddings",
        response_model=EmbeddingResponse,
    )
    async def embeddings(
        request: EmbeddingRequest,
    ) -> EmbeddingResponse:
        """Строит bounded text/image/mixed embeddings."""
        if (
            len(
                request.inputs,
            )
            > resolved_settings.model.max_batch_size
        ):
            raise HTTPException(
                status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
                detail="Embedding batch превышает safety limit.",
            )

        runtime_inputs = tuple(
            RuntimeEmbeddingInput(
                text=item.text,
                image_bytes=_decode_image(
                    item.image_base64,
                    max_image_bytes=(resolved_settings.model.max_image_bytes),
                ),
                instruction=item.instruction,
            )
            for item in request.inputs
        )

        try:
            vectors = await resolved_runtime.embed(
                runtime_inputs,
            )

        except GpuAdmissionError as error:
            raise HTTPException(
                status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
                detail=str(
                    error,
                ),
            ) from error

        except (
            MultimodalModelExecutionError,
            MultimodalRuntimeError,
        ) as error:
            raise HTTPException(
                status_code=(status.HTTP_500_INTERNAL_SERVER_ERROR),
                detail=str(
                    error,
                ),
            ) from error

        return EmbeddingResponse(
            embeddings=vectors,
            model=resolved_settings.model.name,
            dimension=(resolved_settings.model.output_dimension),
        )

    @application.post(
        "/internal/v1/release",
        response_model=ModelStatusResponse,
    )
    async def release_model() -> ModelStatusResponse:
        """Явно освобождает checkpoint после T-indexing job."""
        await resolved_runtime.release()

        runtime_status = await resolved_runtime.status()

        return ModelStatusResponse(
            model=resolved_settings.model.name,
            model_loaded=runtime_status.model_loaded,
            cuda_available=runtime_status.cuda_available,
            free_vram_bytes=(runtime_status.free_vram_bytes),
            total_vram_bytes=(runtime_status.total_vram_bytes),
        )

    return application


app = create_app()
