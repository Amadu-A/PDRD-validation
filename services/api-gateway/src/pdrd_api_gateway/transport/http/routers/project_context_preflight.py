# services/api-gateway/src/pdrd_api_gateway/transport/http/routers/project_context_preflight.py

"""Public HTTP API preflight-проверки Project Context / ПЗ."""

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)

from pdrd_api_gateway.application.ports.project_context_preflight import (
    InvalidProjectContextPreflightError,
    ProjectContextPreflightUnavailableError,
)
from pdrd_api_gateway.core.container import (
    ApplicationContainer,
)
from pdrd_api_gateway.transport.http.dependencies import (
    get_container,
)
from pdrd_api_gateway.transport.http.schemas.project_context_preflight import (
    ProjectContextPreflightResponse,
    ProjectContextPreflightWarningResponse,
)

router = APIRouter(
    prefix="/api/v1/analyses/project-context",
    tags=[
        "analyses",
    ],
)


@router.post(
    "/preflight",
    response_model=ProjectContextPreflightResponse,
)
async def preflight_project_context(
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
    ],
    pdf: Annotated[
        UploadFile,
        File(
            ...,
        ),
    ],
    note_start_page: Annotated[
        int,
        Form(
            ...,
        ),
    ],
    note_end_page: Annotated[
        int,
        Form(
            ...,
        ),
    ],
) -> ProjectContextPreflightResponse:
    """Проверяет диапазон ПЗ и заранее готовит reusable cache."""
    coordinator = container.project_context_preflight

    if coordinator is None:
        raise HTTPException(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=("Project Context preflight не настроен."),
        )

    if note_start_page < 1 or note_end_page <= note_start_page:
        raise HTTPException(
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail=(
                "Начальная страница ПЗ должна быть "
                "положительной, а конечная — больше начальной."
            ),
        )

    max_upload_bytes = container.settings.storage.max_upload_bytes

    try:
        pdf_content = await pdf.read(
            max_upload_bytes + 1,
        )

    finally:
        await pdf.close()

    if not pdf_content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Загруженный PDF-файл пуст.",
        )

    if (
        len(
            pdf_content,
        )
        > max_upload_bytes
    ):
        raise HTTPException(
            status_code=(status.HTTP_413_CONTENT_TOO_LARGE),
            detail=("Размер PDF превышает допустимый предел."),
        )

    try:
        result = await coordinator.execute(
            pdf_content=pdf_content,
            file_name=(pdf.filename or "document.pdf"),
            start_page=note_start_page,
            end_page=note_end_page,
        )

    except InvalidProjectContextPreflightError as error:
        raise HTTPException(
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail=str(
                error,
            ),
        ) from error

    except ProjectContextPreflightUnavailableError as error:
        raise HTTPException(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=str(
                error,
            ),
        ) from error

    return ProjectContextPreflightResponse(
        cache_hit=result.cache_hit,
        cache_built=result.cache_built,
        pages_count=result.pages_count,
        requires_confirmation=(result.requires_confirmation),
        warnings=[
            ProjectContextPreflightWarningResponse(
                page_number=warning.page_number,
                kind=warning.kind,
                confidence=warning.confidence,
                reason=warning.reason,
            )
            for warning in result.warnings
        ],
    )
