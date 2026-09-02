# services/api-gateway/src/pdrd_api_gateway/application/use_cases/create_analysis_job.py

"""Use case создания нового задания анализа."""

from dataclasses import dataclass
from uuid import UUID

from pdrd_api_gateway.application.ports.persistence import (
    UnitOfWorkFactory,
)
from pdrd_api_gateway.domain.analysis_job import AnalysisJob
from pdrd_api_gateway.domain.outbox import OutboxMessage


@dataclass(frozen=True, slots=True)
class CreateAnalysisJob:
    """Атомарно создаёт job и transactional outbox сообщение."""

    unit_of_work_factory: UnitOfWorkFactory

    async def execute(
        self,
        *,
        document_id: UUID,
    ) -> AnalysisJob:
        """Создаёт задание и сообщение для дальнейшей публикации."""
        job = AnalysisJob.create(
            document_id=document_id,
        )

        message = OutboxMessage.analysis_requested(
            job_id=job.id,
        )

        async with self.unit_of_work_factory() as unit_of_work:
            await unit_of_work.analysis_jobs.add(
                job,
            )

            await unit_of_work.outbox.add(
                message,
            )

            await unit_of_work.commit()

        return job
