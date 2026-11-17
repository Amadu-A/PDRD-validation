# services/document-service/src/pdrd_document_service/main.py

"""Точка входа FastAPI и безопасные ответы эксплуатационных ошибок Document Service."""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from pdrd_document_service.application.ports.specification_extraction import (
    ExtractionArtifactStorageError,
)
from pdrd_document_service.core.container import (
    ApplicationContainer,
    build_container,
)
from pdrd_document_service.transport.http.routers.cad import (
    router as cad_router,
)
from pdrd_document_service.transport.http.routers.combined import (
    router as combined_router,
)
from pdrd_document_service.transport.http.routers.equipment_document import (
    router as equipment_document_router,
)
from pdrd_document_service.transport.http.routers.health import (
    router as health_router,
)
from pdrd_document_service.transport.http.routers.pdf import (
    router as pdf_router,
)
from pdrd_document_service.transport.http.routers.pdf_annotation import (
    router as pdf_annotation_router,
)
from pdrd_document_service.transport.http.routers.pdf_crop import (
    router as pdf_crop_router,
)


def create_app(
    container: ApplicationContainer | None = None,
) -> FastAPI:
    """Создаёт настроенное FastAPI-приложение."""
    application_container = container if container is not None else build_container()

    settings = application_container.settings

    application = FastAPI(
        title=settings.service_name,
        version=settings.service_version,
        docs_url=("/docs" if settings.docs_enabled else None),
        redoc_url=("/redoc" if settings.docs_enabled else None),
        openapi_url=("/openapi.json" if settings.docs_enabled else None),
    )

    application.state.container = application_container

    @application.exception_handler(ExtractionArtifactStorageError)
    async def extraction_storage_unavailable(
        request: Request, error: ExtractionArtifactStorageError
    ) -> JSONResponse:
        """Сообщает эксплуатационный отказ явно, без путей, ключей и потери результата."""
        logging.getLogger(__name__).error(
            "extraction_storage status=unavailable path=%s error_type=%s",
            request.url.path,
            type(error.__cause__).__name__,
        )
        return JSONResponse(
            status_code=503,
            content={
                "detail": {
                    "code": "extraction_storage_unavailable",
                    "message": "Хранилище результатов извлечения недоступно. Проверьте права тома Document Service.",
                }
            },
        )

    application.include_router(
        health_router,
    )

    application.include_router(
        pdf_router,
    )
    application.include_router(equipment_document_router)

    application.include_router(
        pdf_annotation_router,
    )
    application.include_router(pdf_crop_router)

    application.include_router(
        cad_router,
    )

    application.include_router(
        combined_router,
    )

    return application


app = create_app()
