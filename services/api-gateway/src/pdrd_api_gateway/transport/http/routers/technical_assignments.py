# services/api-gateway/src/pdrd_api_gateway/transport/http/routers/technical_assignments.py

"""Public T preparation/content API."""

from typing import Annotated
from uuid import (
    UUID,
    uuid4,
)

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel

from pdrd_api_gateway.application.ports.technical_assignment_content import (
    TechnicalAssignmentContentError,
    TechnicalAssignmentContentNotFoundError,
    TechnicalAssignmentContentReader,
)
from pdrd_api_gateway.application.ports.technical_assignment_index import (
    TechnicalAssignmentIndexCoordinator,
    TechnicalAssignmentIndexError,
)
from pdrd_api_gateway.core.container import (
    ApplicationContainer,
)
from pdrd_api_gateway.domain.technical_assignment import (
    InvalidTechnicalAssignmentSnapshotError,
    TechnicalAssignmentSnapshot,
)
from pdrd_api_gateway.transport.http.dependencies import (
    get_container,
)

router = APIRouter(
    prefix="/api/v1/normative/technical-assignments",
    tags=[
        "technical-assignments",
    ],
)


class TechnicalAssignmentPreparationResponse(
    BaseModel,
):
    """Public preflight lifecycle ТЗ."""

    technical_assignment_id: UUID

    analysis_document_id: UUID

    section_id: UUID

    source_file: str

    mime_type: str

    size_bytes: int

    sha256: str

    index_status: str

    index_error: str | None


class TechnicalAssignmentStatusResponse(
    BaseModel,
):
    """Public status уже зарегистрированного ТЗ."""

    technical_assignment_id: UUID

    index_status: str

    index_error: str | None


def _require_reader(
    container: ApplicationContainer,
) -> TechnicalAssignmentContentReader:
    """Возвращает configured T reader."""
    reader = container.technical_assignment_content_reader

    if reader is None:
        raise HTTPException(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=("Technical assignment content reader не настроен."),
        )

    return reader


def _require_index_coordinator(
    container: ApplicationContainer,
) -> TechnicalAssignmentIndexCoordinator:
    """Возвращает configured T lifecycle coordinator."""
    coordinator = container.technical_assignment_index_coordinator

    if coordinator is None:
        raise HTTPException(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=("Technical assignment index coordinator не настроен."),
        )

    return coordinator


@router.post(
    "/prepare",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TechnicalAssignmentPreparationResponse,
)
async def prepare_technical_assignment(
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
    ],
    file: Annotated[
        UploadFile,
        File(),
    ],
    section_id: Annotated[
        UUID,
        Form(),
    ],
) -> TechnicalAssignmentPreparationResponse:
    """Сохраняет ТЗ и запускает индексацию до analysis submit."""
    limit = container.settings.technical_assignment.max_upload_bytes

    try:
        content = await file.read(
            limit + 1,
        )

    finally:
        await file.close()

    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Файл ТЗ пуст.",
        )

    if len(content) > limit:
        raise HTTPException(
            status_code=(status.HTTP_413_CONTENT_TOO_LARGE),
            detail=("Размер ТЗ превышает configured limit."),
        )

    try:
        snapshot = TechnicalAssignmentSnapshot.create(
            analysis_document_id=uuid4(),
            section_id=section_id,
            source_file=file.filename or "",
            content=content,
        )

    except InvalidTechnicalAssignmentSnapshotError as error:
        raise HTTPException(
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail=str(
                error,
            ),
        ) from error

    coordinator = _require_index_coordinator(
        container,
    )

    try:
        state = await coordinator.register(
            snapshot=snapshot,
            content=content,
        )

    except TechnicalAssignmentIndexError as error:
        raise HTTPException(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=str(
                error,
            ),
        ) from error

    return TechnicalAssignmentPreparationResponse(
        technical_assignment_id=(snapshot.technical_assignment_id),
        analysis_document_id=(snapshot.analysis_document_id),
        section_id=snapshot.section_id,
        source_file=snapshot.source_file,
        mime_type=snapshot.mime_type,
        size_bytes=snapshot.size_bytes,
        sha256=snapshot.sha256,
        index_status=state.index_status,
        index_error=state.index_error,
    )


@router.get(
    "/{technical_assignment_id}/status",
    response_model=TechnicalAssignmentStatusResponse,
)
async def get_technical_assignment_status(
    technical_assignment_id: UUID,
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
    ],
) -> TechnicalAssignmentStatusResponse:
    """Проксирует актуальный T-index lifecycle."""
    coordinator = _require_index_coordinator(
        container,
    )

    try:
        state = await coordinator.get_status(
            technical_assignment_id=(technical_assignment_id),
        )

    except TechnicalAssignmentIndexError as error:
        raise HTTPException(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=str(
                error,
            ),
        ) from error

    return TechnicalAssignmentStatusResponse(
        technical_assignment_id=(state.technical_assignment_id),
        index_status=state.index_status,
        index_error=state.index_error,
    )


@router.get(
    "/{technical_assignment_id}/content",
)
async def get_technical_assignment_content(
    technical_assignment_id: UUID,
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
    ],
) -> Response:
    """Возвращает T PDF-preview через Gateway."""
    reader = _require_reader(
        container,
    )

    try:
        content = await reader.get_content(
            technical_assignment_id=(technical_assignment_id),
        )

    except TechnicalAssignmentContentNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(
                error,
            ),
        ) from error

    except TechnicalAssignmentContentError as error:
        raise HTTPException(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=str(
                error,
            ),
        ) from error

    return Response(
        content=content.content,
        media_type=content.mime_type,
        headers={
            "Content-Disposition": "inline",
        },
    )
