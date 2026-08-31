# services/api-gateway/src/pdrd_api_gateway/transport/http/routers/analyses.py

"""HTTP API асинхронных заданий анализа."""

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from pdrd_api_gateway.application.use_cases.create_analysis_job import (
    CreateAnalysisJob,
)
from pdrd_api_gateway.application.use_cases.get_analysis_job import (
    GetAnalysisJob,
)
from pdrd_api_gateway.core.container import ApplicationContainer
from pdrd_api_gateway.transport.http.dependencies import get_container
from pdrd_api_gateway.transport.http.schemas.analyses import (
    AnalysisAcceptedResponse,
    AnalysisStatusResponse,
    CreateAnalysisRequest,
)

router = APIRouter(
    prefix="/api/v1/analyses",
    tags=["analyses"],
)


def require_create_analysis_job(
    container: ApplicationContainer,
) -> CreateAnalysisJob:
    """Возвращает настроенный CreateAnalysisJob use case."""
    if container.create_analysis_job is None:
        raise RuntimeError(
            "CreateAnalysisJob is not configured.",
        )

    return container.create_analysis_job


def require_get_analysis_job(
    container: ApplicationContainer,
) -> GetAnalysisJob:
    """Возвращает настроенный GetAnalysisJob use case."""
    if container.get_analysis_job is None:
        raise RuntimeError(
            "GetAnalysisJob is not configured.",
        )

    return container.get_analysis_job


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AnalysisAcceptedResponse,
)
async def create_analysis(
    request: CreateAnalysisRequest,
    container: Annotated[
        ApplicationContainer,
        Depends(get_container),
    ],
) -> AnalysisAcceptedResponse:
    """Создаёт надёжное асинхронное задание анализа."""
    use_case = require_create_analysis_job(
        container,
    )

    job = await use_case.execute(
        document_id=request.document_id,
    )

    return AnalysisAcceptedResponse(
        job_id=job.id,
        status=job.status,
        status_url=(f"/api/v1/analyses/{job.id}"),
    )


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
