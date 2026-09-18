# services/api-gateway/src/pdrd_api_gateway/application/use_cases/get_analysis_job.py

"""Use case получения состояния задания анализа."""

from dataclasses import dataclass
from uuid import UUID

from pdrd_api_gateway.application.ports.persistence import (
    UnitOfWorkFactory,
)
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJob,
    AnalysisJobStatus,
)


@dataclass(frozen=True, slots=True)
class GetAnalysisJob:
    """Получает задание анализа из persistence layer."""

    unit_of_work_factory: UnitOfWorkFactory

    async def execute(
        self,
        *,
        job_id: UUID,
    ) -> AnalysisJob | None:
        """Возвращает job либо None."""
        async with self.unit_of_work_factory() as unit_of_work:
            return await unit_of_work.analysis_jobs.get(
                job_id,
            )

    async def queue_position(
        self,
        *,
        job: AnalysisJob,
    ) -> int | None:
        """Возвращает 1-based позицию только для ожидающего job."""
        if job.status not in {
            AnalysisJobStatus.PENDING,
            AnalysisJobStatus.QUEUED,
        }:
            return None

        async with self.unit_of_work_factory() as unit_of_work:
            waiting_before = await unit_of_work.analysis_jobs.count_waiting_before(
                created_at=job.created_at,
                job_id=job.id,
            )

        return waiting_before + 1
