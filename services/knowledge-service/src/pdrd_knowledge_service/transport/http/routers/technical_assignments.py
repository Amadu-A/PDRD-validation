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
    Query,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel

from pdrd_knowledge_service.application.ports.technical_assignment_requirement_reader import (
    TechnicalAssignmentRequirementReaderError,
)
from pdrd_knowledge_service.application.use_cases.technical_assignment_requirements import (
    TECHNICAL_ASSIGNMENT_REQUIREMENT_DEFAULT_PAGE_SIZE,
    TECHNICAL_ASSIGNMENT_REQUIREMENT_MAX_PAGE_SIZE,
    ListTechnicalAssignmentRequirements,
    TechnicalAssignmentRequirementsConflictError,
)
from pdrd_knowledge_service.application.use_cases.technical_assignments import (
    GetTechnicalAssignment,
    GetTechnicalAssignmentContent,
    RegisterTechnicalAssignment,
    TechnicalAssignmentContentUnavailableError,
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
    tags=[
        "technical-assignments",
    ],
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


class TechnicalAssignmentRequirementResponse(
    BaseModel,
):
    """HTTP representation одного atomic T requirement."""

    point_id: str

    requirement_id: str

    requirement_index: int

    page: int

    requirement_strength: str

    scopes: list[str]

    normative_refs: list[str]

    source_text: str

    text: str


class TechnicalAssignmentRequirementListResponse(
    BaseModel,
):
    """Bounded deterministic page atomic requirements."""

    technical_assignment_id: UUID

    analysis_document_id: UUID

    section_id: UUID

    source_file: str

    source_sha256: str

    total: int

    offset: int

    limit: int

    requirements: list[TechnicalAssignmentRequirementResponse]


def _response(
    assignment: TechnicalAssignment,
) -> TechnicalAssignmentResponse:
    """Domain -> HTTP schema."""
    return TechnicalAssignmentResponse(
        technical_assignment_id=assignment.technical_assignment_id,
        analysis_document_id=assignment.analysis_document_id,
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


def _require_content(
    container: ApplicationContainer,
) -> GetTechnicalAssignmentContent:
    """Возвращает configured preview use case."""
    if container.get_technical_assignment_content is None:
        raise RuntimeError(
            "GetTechnicalAssignmentContent is not configured.",
        )

    return container.get_technical_assignment_content


def _require_requirements(
    container: ApplicationContainer,
) -> ListTechnicalAssignmentRequirements:
    """Возвращает configured T-first requirement feed."""
    if container.list_technical_assignment_requirements is None:
        raise RuntimeError(
            "ListTechnicalAssignmentRequirements is not configured.",
        )

    return container.list_technical_assignment_requirements


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
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
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
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(
                error,
            ),
        ) from error

    except TechnicalAssignmentRegistrationConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(
                error,
            ),
        ) from error

    return _response(
        assignment,
    )


@router.get(
    "/{technical_assignment_id}/requirements",
    response_model=TechnicalAssignmentRequirementListResponse,
)
async def list_technical_assignment_requirements(
    technical_assignment_id: UUID,
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
    ],
    offset: Annotated[
        int,
        Query(
            ge=0,
        ),
    ] = 0,
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=(TECHNICAL_ASSIGNMENT_REQUIREMENT_MAX_PAGE_SIZE),
        ),
    ] = TECHNICAL_ASSIGNMENT_REQUIREMENT_DEFAULT_PAGE_SIZE,
) -> TechnicalAssignmentRequirementListResponse:
    """Возвращает atomic T-R feed для independent validation."""
    use_case = _require_requirements(
        container,
    )

    try:
        result = await use_case.execute(
            technical_assignment_id=technical_assignment_id,
            offset=offset,
            limit=limit,
        )

    except TechnicalAssignmentNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(
                error,
            ),
        ) from error

    except TechnicalAssignmentRequirementsConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(
                error,
            ),
        ) from error

    except TechnicalAssignmentRequirementReaderError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(
                error,
            ),
        ) from error

    return TechnicalAssignmentRequirementListResponse(
        technical_assignment_id=(result.technical_assignment_id),
        analysis_document_id=(result.analysis_document_id),
        section_id=result.section_id,
        source_file=result.source_file,
        source_sha256=result.source_sha256,
        total=result.total,
        offset=result.offset,
        limit=result.limit,
        requirements=[
            TechnicalAssignmentRequirementResponse(
                point_id=requirement.point_id,
                requirement_id=(requirement.requirement_id),
                requirement_index=(requirement.requirement_index),
                page=requirement.page,
                requirement_strength=(requirement.strength),
                scopes=list(
                    requirement.scopes,
                ),
                normative_refs=list(
                    requirement.normative_refs,
                ),
                source_text=(requirement.source_text),
                text=requirement.text,
            )
            for requirement in result.requirements
        ],
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
    """Возвращает inline PDF или Word PDF-preview ТЗ."""
    use_case = _require_content(
        container,
    )

    try:
        content = await use_case.execute(
            technical_assignment_id=technical_assignment_id,
        )

    except TechnicalAssignmentNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(
                error,
            ),
        ) from error

    except TechnicalAssignmentContentUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
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
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(
                error,
            ),
        ) from error

    return _response(
        assignment,
    )
