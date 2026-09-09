# services/api-gateway/src/pdrd_api_gateway/transport/http/schemas/analyses.py

"""HTTP schemas асинхронных заданий анализа."""

from datetime import datetime
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJobStatus,
)


class TechnicalAssignmentSnapshotResponse(
    BaseModel,
):
    """Immutable metadata загруженного ТЗ."""

    model_config = ConfigDict(
        frozen=True,
    )

    technical_assignment_id: UUID

    analysis_document_id: UUID

    section_id: UUID

    source_file: str

    mime_type: str

    size_bytes: int

    sha256: str


class AnalysisAcceptedResponse(BaseModel):
    """Ответ на успешно принятое асинхронное задание."""

    model_config = ConfigDict(
        frozen=True,
    )

    job_id: UUID

    document_id: UUID

    status: AnalysisJobStatus

    status_url: str

    normative_section_id: UUID | None

    normative_document_ids: list[UUID]

    user_package_document_ids: list[UUID] = Field(
        default_factory=list,
    )

    technical_assignment: TechnicalAssignmentSnapshotResponse | None = None


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

    normative_section_id: UUID | None

    normative_document_ids: list[UUID]

    user_package_document_ids: list[UUID] = Field(
        default_factory=list,
    )

    technical_assignment: TechnicalAssignmentSnapshotResponse | None = None

    created_at: datetime

    updated_at: datetime
