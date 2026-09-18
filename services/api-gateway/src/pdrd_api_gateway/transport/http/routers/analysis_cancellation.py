# services/api-gateway/src/pdrd_api_gateway/transport/http/routers/analysis_cancellation.py

"""HTTP API отмены analysis jobs."""

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from pydantic import (
    BaseModel,
    ConfigDict,
)

from pdrd_api_gateway.application.use_cases.cancel_analysis_job import (
    AnalysisJobCancellationConflictError,
    AnalysisJobCancellationNotFoundError,
    CancelAnalysisJob,
)
from pdrd_api_gateway.core.container import (
    ApplicationContainer,
)
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJobStatus,
)
from pdrd_api_gateway.transport.http.dependencies import (
    get_container,
)

router = APIRouter(
    tags=[
        "analyses",
    ],
)


class AnalysisCancellationResponse(
    BaseModel,
):
    """Результат подтверждённой отмены analysis job."""

    model_config = ConfigDict(
        frozen=True,
    )

    job_id: UUID

    status: AnalysisJobStatus


def require_cancel_analysis_job(
    container: ApplicationContainer,
) -> CancelAnalysisJob:
    """Возвращает настроенный CancelAnalysisJob."""
    if container.cancel_analysis_job is None:
        raise RuntimeError(
            "CancelAnalysisJob is not configured.",
        )

    return container.cancel_analysis_job


@router.post(
    "/api/v1/analyses/{job_id}/cancel",
    response_model=AnalysisCancellationResponse,
)
async def cancel_analysis(
    job_id: UUID,
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
    ],
) -> AnalysisCancellationResponse:
    """Отменяет pending, queued или processing analysis job."""
    use_case = require_cancel_analysis_job(
        container,
    )

    try:
        job = await use_case.execute(
            job_id=job_id,
        )

    except AnalysisJobCancellationNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(
                error,
            ),
        ) from error

    except AnalysisJobCancellationConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(
                error,
            ),
        ) from error

    return AnalysisCancellationResponse(
        job_id=job.id,
        status=job.status,
    )
