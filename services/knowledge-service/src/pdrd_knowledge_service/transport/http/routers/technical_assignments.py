# services/knowledge-service/src/pdrd_knowledge_service/transport/http/routers/technical_assignments.py

"""Internal HTTP API lifecycle технического задания."""

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from pydantic import BaseModel

from pdrd_knowledge_service.application.use_cases.technical_assignments import (
    GetTechnicalAssignment,
    RegisterTechnicalAssignment,
    TechnicalAssignmentNotFoundError,
    TechnicalAssignmentRegistrationConflictError,
    TechnicalAssignmentUploadError,
)
from pdrd_knowledge_service.core.container import (
    ApplicationContainer,
)
from pdrd_knowledge_service.domain.technical_assignment import (
    TechnicalAssignment,
)
from pdrd_knowledge_service.transport.http.dependencies import (
    get_container,
)

router = APIRouter(
    prefix="/internal/v1/technical-assignments",
    tags=["technical-assignments"],
)


class TechnicalAssignmentResponse(
    BaseModel,
):
    """HTTP representation T lifecycle."""

    technical_assignment_id: UUID

    analysis_document_id: UUID

    section_id: UUID

    source_file: str

    mime_type: str

    size_bytes: int

    sha256: str

    index_status: str

    index_error: str | None


def _response(
    assignment: TechnicalAssignment,
) -> TechnicalAssignmentResponse:
    """Domain -> HTTP schema."""
    return TechnicalAssignmentResponse(
        technical_assignment_id=(assignment.technical_assignment_id),
        analysis_document_id=(assignment.analysis_document_id),
        section_id=assignment.section_id,
        source_file=assignment.original_name,
        mime_type=assignment.mime_type,
        size_bytes=assignment.size_bytes,
        sha256=assignment.sha256,
        index_status=assignment.index_status.value,
        index_error=assignment.index_error,
    )


def _require_register(
    container: ApplicationContainer,
) -> RegisterTechnicalAssignment:
    """Возвращает configured use case."""
    if container.register_technical_assignment is None:
        raise RuntimeError(
            "RegisterTechnicalAssignment is not configured.",
        )

    return container.register_technical_assignment


def _require_get(
    container: ApplicationContainer,
) -> GetTechnicalAssignment:
    """Возвращает configured use case."""
    if container.get_technical_assignment is None:
        raise RuntimeError(
            "GetTechnicalAssignment is not configured.",
        )

    return container.get_technical_assignment


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TechnicalAssignmentResponse,
)
async def register_technical_assignment(
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
    technical_assignment_id: Annotated[
        UUID,
        Form(),
    ],
    analysis_document_id: Annotated[
        UUID,
        Form(),
    ],
    section_id: Annotated[
        UUID,
        Form(),
    ],
    source_file: Annotated[
        str,
        Form(),
    ],
    sha256: Annotated[
        str,
        Form(),
    ],
) -> TechnicalAssignmentResponse:
    """Идемпотентно регистрирует ТЗ для background indexing."""
    limit = container.settings.technical_assignment.max_upload_bytes

    try:
        content = await file.read(
            limit + 1,
        )

    finally:
        await file.close()

    if (
        len(
            content,
        )
        > limit
    ):
        raise HTTPException(
            status_code=(status.HTTP_413_CONTENT_TOO_LARGE),
            detail="Размер ТЗ превышает configured limit.",
        )

    use_case = _require_register(
        container,
    )

    try:
        assignment = await use_case.execute(
            technical_assignment_id=technical_assignment_id,
            analysis_document_id=analysis_document_id,
            section_id=section_id,
            original_name=source_file,
            expected_sha256=sha256,
            content=content,
        )

    except TechnicalAssignmentUploadError as error:
        raise HTTPException(
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail=str(
                error,
            ),
        ) from error

    except TechnicalAssignmentRegistrationConflictError as error:
        raise HTTPException(
            status_code=(status.HTTP_409_CONFLICT),
            detail=str(
                error,
            ),
        ) from error

    return _response(
        assignment,
    )


@router.get(
    "/{technical_assignment_id}",
    response_model=TechnicalAssignmentResponse,
)
async def get_technical_assignment(
    technical_assignment_id: UUID,
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
    ],
) -> TechnicalAssignmentResponse:
    """Возвращает indexing lifecycle."""
    use_case = _require_get(
        container,
    )

    try:
        assignment = await use_case.execute(
            technical_assignment_id=technical_assignment_id,
        )

    except TechnicalAssignmentNotFoundError as error:
        raise HTTPException(
            status_code=(status.HTTP_404_NOT_FOUND),
            detail=str(
                error,
            ),
        ) from error

    return _response(
        assignment,
    )
