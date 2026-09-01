# services/api-gateway/src/pdrd_api_gateway/transport/http/routers/analyses.py

"""HTTP API асинхронных заданий анализа."""

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

from pdrd_api_gateway.application.use_cases.get_analysis_job import (
    GetAnalysisJob,
)
from pdrd_api_gateway.application.use_cases.get_analysis_result import (
    AnalysisResultJobNotFoundError,
    AnalysisResultNotReadyError,
    AnalysisResultUnavailableError,
    GetAnalysisResult,
)
from pdrd_api_gateway.application.use_cases.submit_analysis import (
    EmptyAnalysisFileError,
    SubmitAnalysis,
)
from pdrd_api_gateway.core.container import (
    ApplicationContainer,
)
from pdrd_api_gateway.domain.analysis_submission import (
    InvalidAnalysisSubmissionError,
)
from pdrd_api_gateway.transport.http.dependencies import (
    get_container,
)
from pdrd_api_gateway.transport.http.schemas.analyses import (
    AnalysisAcceptedResponse,
    AnalysisStatusResponse,
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
) -> tuple[bytes | None, str | None]:
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

    if len(content) > max_upload_bytes:
        raise HTTPException(
            status_code=(status.HTTP_413_CONTENT_TOO_LARGE),
            detail=("Размер загруженного файла превышает допустимый предел."),
        )

    return (
        content,
        upload.filename,
    )


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AnalysisAcceptedResponse,
)
async def create_analysis(
    container: Annotated[
        ApplicationContainer,
        Depends(get_container),
    ],
    pdf: Annotated[
        UploadFile | None,
        File(),
    ] = None,
    cad: Annotated[
        UploadFile | None,
        File(),
    ] = None,
    pages: Annotated[
        str | None,
        Form(),
    ] = None,
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

    use_case = require_submit_analysis(
        container,
    )

    try:
        job = await use_case.execute(
            pdf_content=pdf_content,
            pdf_file_name=pdf_file_name,
            cad_content=cad_content,
            cad_file_name=cad_file_name,
            pages=pages,
        )
    except EmptyAnalysisFileError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(
                error,
            ),
        ) from error
    except InvalidAnalysisSubmissionError as error:
        raise HTTPException(
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail=str(
                error,
            ),
        ) from error

    if job.document_id is None:
        raise RuntimeError(
            "Created analysis job has no document_id.",
        )

    return AnalysisAcceptedResponse(
        job_id=job.id,
        document_id=job.document_id,
        status=job.status,
        status_url=(f"/api/v1/analyses/{job.id}"),
    )


@router.get(
    "/{job_id}/result",
    response_model=dict[str, Any],
)
async def get_analysis_result(
    job_id: UUID,
    container: Annotated[
        ApplicationContainer,
        Depends(get_container),
    ],
) -> dict[str, Any]:
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
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(
                error,
            ),
        ) from error

    except AnalysisResultNotReadyError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
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
        Depends(get_container),
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
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis job not found.",
        )

    return AnalysisStatusResponse(
        job_id=job.id,
        document_id=job.document_id,
        status=job.status,
        attempt_count=job.attempt_count,
        error_code=job.error_code,
        error_message=job.error_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )
