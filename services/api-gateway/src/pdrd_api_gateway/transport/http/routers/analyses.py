# services/api-gateway/src/pdrd_api_gateway/transport/http/routers/analyses.py

"""HTTP API асинхронных заданий анализа."""

import json
from typing import (
    Annotated,
    Any,
)
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

from pdrd_api_gateway.application.ports.normative_catalog import (
    NormativeCatalogReadError,
)
from pdrd_api_gateway.application.ports.normative_catalog_management import (
    NormativeCatalogUnavailableError,
)
from pdrd_api_gateway.application.use_cases.get_analysis_job import (
    GetAnalysisJob,
)
from pdrd_api_gateway.application.use_cases.get_analysis_result import (
    AnalysisResultJobNotFoundError,
    AnalysisResultNotReadyError,
    AnalysisResultUnavailableError,
    GetAnalysisResult,
)
from pdrd_api_gateway.application.use_cases.resolve_normative_snapshot import (
    InvalidNormativeSelectionError,
    NormativeSelectionConflictError,
    UserPackageReaderNotConfiguredError,
)
from pdrd_api_gateway.application.use_cases.submit_analysis import (
    EmptyAnalysisFileError,
    NormativeSnapshotResolverNotConfiguredError,
    SubmitAnalysis,
)
from pdrd_api_gateway.core.container import (
    ApplicationContainer,
)
from pdrd_api_gateway.domain.analysis_submission import (
    InvalidAnalysisSubmissionError,
)
from pdrd_api_gateway.domain.normative_snapshot import (
    InvalidNormativeAnalysisSnapshotError,
    NormativeAnalysisSnapshot,
)
from pdrd_api_gateway.domain.technical_assignment import (
    InvalidTechnicalAssignmentSnapshotError,
)
from pdrd_api_gateway.transport.http.dependencies import (
    get_container,
)
from pdrd_api_gateway.transport.http.schemas.analyses import (
    AnalysisAcceptedResponse,
    AnalysisStatusResponse,
    TechnicalAssignmentSnapshotResponse,
)

router = APIRouter(
    prefix="/api/v1/analyses",
    tags=["analyses"],
)


def require_submit_analysis(
    container: ApplicationContainer,
) -> SubmitAnalysis:
    """Возвращает настроенный SubmitAnalysis use case."""
    if container.submit_analysis is None:
        raise RuntimeError(
            "SubmitAnalysis is not configured.",
        )

    return container.submit_analysis


def require_get_analysis_job(
    container: ApplicationContainer,
) -> GetAnalysisJob:
    """Возвращает настроенный GetAnalysisJob use case."""
    if container.get_analysis_job is None:
        raise RuntimeError(
            "GetAnalysisJob is not configured.",
        )

    return container.get_analysis_job


def require_get_analysis_result(
    container: ApplicationContainer,
) -> GetAnalysisResult:
    """Возвращает настроенный GetAnalysisResult use case."""
    if container.get_analysis_result is None:
        raise RuntimeError(
            "GetAnalysisResult is not configured.",
        )

    return container.get_analysis_result


async def read_upload(
    *,
    upload: UploadFile | None,
    max_upload_bytes: int,
) -> tuple[
    bytes | None,
    str | None,
]:
    """Читает upload с ограничением максимального размера."""
    if upload is None:
        return (
            None,
            None,
        )

    try:
        content = await upload.read(
            max_upload_bytes + 1,
        )

    finally:
        await upload.close()

    if (
        len(
            content,
        )
        > max_upload_bytes
    ):
        raise HTTPException(
            status_code=(status.HTTP_413_CONTENT_TOO_LARGE),
            detail=("Размер загруженного файла превышает допустимый предел."),
        )

    return (
        content,
        upload.filename,
    )


def _parse_document_ids(
    raw_value: str | None,
    *,
    field_name: str,
) -> (
    tuple[
        UUID,
        ...,
    ]
    | None
):
    """Разбирает JSON array UUID из multipart form field."""
    if raw_value is None:
        return None

    try:
        payload = json.loads(
            raw_value,
        )

    except json.JSONDecodeError as error:
        raise HTTPException(
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail=(f"{field_name} должен быть JSON array UUID."),
        ) from error

    if not isinstance(
        payload,
        list,
    ):
        raise HTTPException(
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail=(f"{field_name} должен быть JSON array."),
        )

    result: list[UUID] = []

    for value in payload:
        if not isinstance(
            value,
            str,
        ):
            raise HTTPException(
                status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
                detail=(f"Каждый элемент {field_name} должен быть строкой UUID."),
            )

        try:
            result.append(
                UUID(
                    value,
                )
            )

        except ValueError as error:
            raise HTTPException(
                status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
                detail=(f"Некорректный UUID в {field_name}: {value}."),
            ) from error

    return tuple(
        result,
    )


def parse_normative_document_ids(
    raw_value: str | None,
) -> (
    tuple[
        UUID,
        ...,
    ]
    | None
):
    """Разбирает normative_document_ids."""
    return _parse_document_ids(
        raw_value,
        field_name="normative_document_ids",
    )


def parse_user_package_document_ids(
    raw_value: str | None,
) -> (
    tuple[
        UUID,
        ...,
    ]
    | None
):
    """Разбирает user_package_document_ids."""
    return _parse_document_ids(
        raw_value,
        field_name="user_package_document_ids",
    )


def build_technical_assignment_response(
    snapshot: (NormativeAnalysisSnapshot | None),
) -> TechnicalAssignmentSnapshotResponse | None:
    """Преобразует domain snapshot ТЗ в HTTP schema."""
    if snapshot is None or snapshot.technical_assignment is None:
        return None

    technical_assignment = snapshot.technical_assignment

    return TechnicalAssignmentSnapshotResponse(
        technical_assignment_id=(technical_assignment.technical_assignment_id),
        analysis_document_id=(technical_assignment.analysis_document_id),
        section_id=(technical_assignment.section_id),
        source_file=(technical_assignment.source_file),
        mime_type=(technical_assignment.mime_type),
        size_bytes=(technical_assignment.size_bytes),
        sha256=technical_assignment.sha256,
    )


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AnalysisAcceptedResponse,
)
async def create_analysis(
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
    ],
    pdf: Annotated[
        UploadFile | None,
        File(),
    ] = None,
    cad: Annotated[
        UploadFile | None,
        File(),
    ] = None,
    technical_assignment: Annotated[
        UploadFile | None,
        File(),
    ] = None,
    pages: Annotated[
        str | None,
        Form(),
    ] = None,
    use_explanatory_note: Annotated[
        bool,
        Form(),
    ] = False,
    note_start_page: Annotated[
        str | None,
        Form(),
    ] = None,
    note_end_page: Annotated[
        str | None,
        Form(),
    ] = None,
    normative_section_id: Annotated[
        UUID | None,
        Form(),
    ] = None,
    normative_document_ids: Annotated[
        str | None,
        Form(),
    ] = None,
    user_package_document_ids: Annotated[
        str | None,
        Form(),
    ] = None,
    normative_prompt_override_enabled: Annotated[
        bool,
        Form(),
    ] = False,
    normative_prompt_override: Annotated[
        str,
        Form(),
    ] = "",
) -> AnalysisAcceptedResponse:
    """Принимает документы и создаёт asynchronous analysis job."""
    max_upload_bytes = container.settings.storage.max_upload_bytes

    pdf_content, pdf_file_name = await read_upload(
        upload=pdf,
        max_upload_bytes=max_upload_bytes,
    )

    cad_content, cad_file_name = await read_upload(
        upload=cad,
        max_upload_bytes=max_upload_bytes,
    )

    (
        technical_assignment_content,
        technical_assignment_file_name,
    ) = await read_upload(
        upload=technical_assignment,
        max_upload_bytes=(container.settings.technical_assignment.max_upload_bytes),
    )

    parsed_normative_document_ids = parse_normative_document_ids(
        normative_document_ids,
    )

    parsed_user_package_document_ids = parse_user_package_document_ids(
        user_package_document_ids,
    )

    use_case = require_submit_analysis(
        container,
    )

    execute_kwargs: dict[
        str,
        Any,
    ] = {
        "pdf_content": pdf_content,
        "pdf_file_name": pdf_file_name,
        "cad_content": cad_content,
        "cad_file_name": cad_file_name,
        "pages": pages,
        "use_explanatory_note": (use_explanatory_note),
        "note_start_page": note_start_page,
        "note_end_page": note_end_page,
        "normative_section_id": (normative_section_id),
        "normative_document_ids": (parsed_normative_document_ids),
        "user_package_document_ids": (parsed_user_package_document_ids),
        "normative_prompt_override_enabled": (normative_prompt_override_enabled),
        "normative_prompt_override": (normative_prompt_override),
    }

    if technical_assignment_content is not None:
        execute_kwargs["technical_assignment_content"] = technical_assignment_content

        execute_kwargs["technical_assignment_file_name"] = (
            technical_assignment_file_name
        )

    try:
        job = await use_case.execute(
            **execute_kwargs,
        )

    except EmptyAnalysisFileError as error:
        raise HTTPException(
            status_code=(status.HTTP_400_BAD_REQUEST),
            detail=str(
                error,
            ),
        ) from error

    except (
        InvalidAnalysisSubmissionError,
        InvalidNormativeSelectionError,
        InvalidNormativeAnalysisSnapshotError,
        InvalidTechnicalAssignmentSnapshotError,
    ) as error:
        raise HTTPException(
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail=str(
                error,
            ),
        ) from error

    except NormativeSelectionConflictError as error:
        raise HTTPException(
            status_code=(status.HTTP_409_CONFLICT),
            detail=str(
                error,
            ),
        ) from error

    except (
        NormativeCatalogReadError,
        NormativeCatalogUnavailableError,
        NormativeSnapshotResolverNotConfiguredError,
        UserPackageReaderNotConfiguredError,
    ) as error:
        raise HTTPException(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=str(
                error,
            ),
        ) from error

    if job.document_id is None:
        raise RuntimeError(
            "Created analysis job has no document_id.",
        )

    snapshot = job.normative_snapshot

    return AnalysisAcceptedResponse(
        job_id=job.id,
        document_id=job.document_id,
        status=job.status,
        status_url=(f"/api/v1/analyses/{job.id}"),
        normative_section_id=(snapshot.section_id if snapshot is not None else None),
        normative_document_ids=(
            list(
                snapshot.document_ids,
            )
            if snapshot is not None
            else []
        ),
        user_package_document_ids=(
            list(
                snapshot.user_package_document_ids,
            )
            if snapshot is not None
            else []
        ),
        technical_assignment=(
            build_technical_assignment_response(
                snapshot,
            )
        ),
    )


@router.get(
    "/{job_id}/result",
    response_model=dict[
        str,
        Any,
    ],
)
async def get_analysis_result(
    job_id: UUID,
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
    ],
) -> dict[
    str,
    Any,
]:
    """Возвращает JSON-результат завершённого анализа."""
    use_case = require_get_analysis_result(
        container,
    )

    try:
        return await use_case.execute(
            job_id=job_id,
        )

    except AnalysisResultJobNotFoundError as error:
        raise HTTPException(
            status_code=(status.HTTP_404_NOT_FOUND),
            detail=str(
                error,
            ),
        ) from error

    except AnalysisResultNotReadyError as error:
        raise HTTPException(
            status_code=(status.HTTP_409_CONFLICT),
            detail={
                "message": str(
                    error,
                ),
                "status": error.status.value,
            },
        ) from error

    except AnalysisResultUnavailableError as error:
        raise HTTPException(
            status_code=(status.HTTP_500_INTERNAL_SERVER_ERROR),
            detail=str(
                error,
            ),
        ) from error


@router.get(
    "/{job_id}",
    response_model=AnalysisStatusResponse,
)
async def get_analysis(
    job_id: UUID,
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
    ],
) -> AnalysisStatusResponse:
    """Возвращает актуальное состояние задания."""
    use_case = require_get_analysis_job(
        container,
    )

    job = await use_case.execute(
        job_id=job_id,
    )

    if job is None:
        raise HTTPException(
            status_code=(status.HTTP_404_NOT_FOUND),
            detail="Analysis job not found.",
        )

    snapshot = job.normative_snapshot

    return AnalysisStatusResponse(
        job_id=job.id,
        document_id=job.document_id,
        status=job.status,
        attempt_count=job.attempt_count,
        error_code=job.error_code,
        error_message=job.error_message,
        normative_section_id=(snapshot.section_id if snapshot is not None else None),
        normative_document_ids=(
            list(
                snapshot.document_ids,
            )
            if snapshot is not None
            else []
        ),
        user_package_document_ids=(
            list(
                snapshot.user_package_document_ids,
            )
            if snapshot is not None
            else []
        ),
        technical_assignment=(
            build_technical_assignment_response(
                snapshot,
            )
        ),
        created_at=job.created_at,
        updated_at=job.updated_at,
    )
