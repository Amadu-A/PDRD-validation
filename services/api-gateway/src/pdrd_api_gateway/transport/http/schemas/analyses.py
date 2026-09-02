# services/api-gateway/src/pdrd_api_gateway/transport/http/schemas/analyses.py

"""HTTP schemas асинхронных заданий анализа."""

from datetime import datetime
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
)

from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJobStatus,
)


class AnalysisAcceptedResponse(BaseModel):
    """Ответ на успешно принятое асинхронное задание."""

    model_config = ConfigDict(
        frozen=True,
    )

    job_id: UUID
    document_id: UUID

    status: AnalysisJobStatus
    status_url: str


class AnalysisStatusResponse(BaseModel):
    """Текущее состояние задания анализа."""

    model_config = ConfigDict(
        frozen=True,
    )

    job_id: UUID
    document_id: UUID | None

    status: AnalysisJobStatus

    attempt_count: int

    error_code: str | None
    error_message: str | None

    created_at: datetime
    updated_at: datetime
