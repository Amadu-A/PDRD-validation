# services/api-gateway/src/pdrd_api_gateway/transport/http/routers/analysis_progress.py

"""HTTP API progress выполнения analysis jobs."""

from typing import (
    Annotated,
    Literal,
)
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

from pdrd_api_gateway.application.use_cases.get_analysis_job import (
    GetAnalysisJob,
)
from pdrd_api_gateway.application.use_cases.update_analysis_progress import (
    AnalysisProgressJobNotFoundError,
    UpdateAnalysisProgress,
)
from pdrd_api_gateway.core.container import (
    ApplicationContainer,
)
from pdrd_api_gateway.domain.analysis_job import (
    ANALYSIS_PROGRESS_TOTAL,
    AnalysisJob,
    AnalysisJobStatus,
    AnalysisProgressStage,
)
from pdrd_api_gateway.transport.http.dependencies import (
    get_container,
)

router = APIRouter(
    tags=[
        "analysis-progress",
    ],
)


class AnalysisProgressUpdateRequest(
    BaseModel,
):
    """Internal progress callback из n8n."""

    model_config = ConfigDict(
        frozen=True,
    )

    stage: AnalysisProgressStage


class AnalysisProgressUpdateResponse(
    BaseModel,
):
    """Ответ orchestration checkpoint с cancellation signal."""

    model_config = ConfigDict(
        frozen=True,
    )

    status: Literal["ok"] = "ok"

    stage: AnalysisProgressStage

    changed: bool

    cancelled: bool


class AnalysisProgressResponse(
    BaseModel,
):
    """Пользовательское представление текущего progress."""

    model_config = ConfigDict(
        frozen=True,
    )

    stage: AnalysisProgressStage | None

    message: str

    current: int

    total: int

    queue_position: int | None


def require_get_analysis_job(
    container: ApplicationContainer,
) -> GetAnalysisJob:
    """Возвращает настроенный GetAnalysisJob."""
    if container.get_analysis_job is None:
        raise RuntimeError(
            "GetAnalysisJob is not configured.",
        )

    return container.get_analysis_job


def require_update_analysis_progress(
    container: ApplicationContainer,
) -> UpdateAnalysisProgress:
    """Возвращает настроенный UpdateAnalysisProgress."""
    if container.update_analysis_progress is None:
        raise RuntimeError(
            "UpdateAnalysisProgress is not configured.",
        )

    return container.update_analysis_progress


def _progress_response(
    *,
    job: AnalysisJob,
    queue_position: int | None,
) -> AnalysisProgressResponse:
    """Преобразует lifecycle + stage в стабильный UI progress."""
    if job.status in {
        AnalysisJobStatus.PENDING,
        AnalysisJobStatus.QUEUED,
    }:
        return AnalysisProgressResponse(
            stage=None,
            message="Ожидает выполнения.",
            current=0,
            total=ANALYSIS_PROGRESS_TOTAL,
            queue_position=queue_position,
        )

    if job.status is AnalysisJobStatus.PROCESSING:
        if job.progress_stage is None:
            return AnalysisProgressResponse(
                stage=None,
                message="Запускаю анализ…",
                current=0,
                total=ANALYSIS_PROGRESS_TOTAL,
                queue_position=None,
            )

        return AnalysisProgressResponse(
            stage=job.progress_stage,
            message=job.progress_stage.message,
            current=job.progress_stage.current,
            total=ANALYSIS_PROGRESS_TOTAL,
            queue_position=None,
        )

    if job.status is AnalysisJobStatus.COMPLETED:
        return AnalysisProgressResponse(
            stage=AnalysisProgressStage.BUILDING_RESULT,
            message="Анализ завершён.",
            current=ANALYSIS_PROGRESS_TOTAL,
            total=ANALYSIS_PROGRESS_TOTAL,
            queue_position=None,
        )

    if job.status is AnalysisJobStatus.FAILED:
        return AnalysisProgressResponse(
            stage=job.progress_stage,
            message="Анализ завершён с ошибкой.",
            current=(
                job.progress_stage.current if job.progress_stage is not None else 0
            ),
            total=ANALYSIS_PROGRESS_TOTAL,
            queue_position=None,
        )

    return AnalysisProgressResponse(
        stage=job.progress_stage,
        message="Анализ отменён.",
        current=(job.progress_stage.current if job.progress_stage is not None else 0),
        total=ANALYSIS_PROGRESS_TOTAL,
        queue_position=None,
    )


@router.get(
    "/api/v1/analyses/{job_id}/progress",
    response_model=AnalysisProgressResponse,
)
async def get_analysis_progress(
    job_id: UUID,
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
    ],
) -> AnalysisProgressResponse:
    """Возвращает durable stage и позицию задания в очереди."""
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

    queue_position = await use_case.queue_position(
        job=job,
    )

    return _progress_response(
        job=job,
        queue_position=queue_position,
    )


@router.post(
    "/internal/v1/analysis-progress/{document_id}",
    response_model=AnalysisProgressUpdateResponse,
)
async def update_analysis_progress(
    document_id: UUID,
    request: AnalysisProgressUpdateRequest,
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
    ],
) -> AnalysisProgressUpdateResponse:
    """Принимает best-effort progress callback и возвращает cancellation signal."""
    use_case = require_update_analysis_progress(
        container,
    )

    try:
        result = await use_case.execute(
            document_id=document_id,
            stage=request.stage,
        )

    except AnalysisProgressJobNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(
                error,
            ),
        ) from error

    return AnalysisProgressUpdateResponse(
        stage=request.stage,
        changed=result.changed,
        cancelled=result.cancelled,
    )
