# services/api-gateway/src/pdrd_api_gateway/application/use_cases/get_analysis_job.py

"""Use case получения состояния задания анализа."""

from dataclasses import dataclass
from uuid import UUID

from pdrd_api_gateway.application.ports.persistence import (
    UnitOfWorkFactory,
)
from pdrd_api_gateway.domain.analysis_job import AnalysisJob


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
