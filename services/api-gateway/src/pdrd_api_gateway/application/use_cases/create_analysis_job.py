# services/api-gateway/src/pdrd_api_gateway/application/use_cases/create_analysis_job.py

"""Use case создания нового задания анализа."""

from dataclasses import dataclass

from pdrd_api_gateway.application.ports.persistence import (
    UnitOfWorkFactory,
)
from pdrd_api_gateway.domain.analysis_job import AnalysisJob


@dataclass(frozen=True, slots=True)
class CreateAnalysisJob:
    """Создаёт и сохраняет новое задание анализа."""

    unit_of_work_factory: UnitOfWorkFactory

    async def execute(self) -> AnalysisJob:
        """Создаёт задание и атомарно сохраняет его."""
        job = AnalysisJob.create()

        async with self.unit_of_work_factory() as unit_of_work:
            await unit_of_work.analysis_jobs.add(
                job,
            )
            await unit_of_work.commit()

        return job
