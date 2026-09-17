# services/api-gateway/src/pdrd_api_gateway/application/use_cases/cancel_analysis_job.py

"""Use case отмены задания анализа."""

from dataclasses import dataclass
from uuid import UUID

from pdrd_api_gateway.application.ports.persistence import (
    UnitOfWorkFactory,
)
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJob,
    AnalysisJobStatus,
)


class AnalysisJobCancellationNotFoundError(
    LookupError,
):
    """Задание анализа для отмены не найдено."""


class AnalysisJobCancellationConflictError(
    RuntimeError,
):
    """Завершённое задание анализа нельзя отменить."""


@dataclass(frozen=True, slots=True)
class CancelAnalysisJob:
    """Отменяет active analysis job под PostgreSQL row lock."""

    unit_of_work_factory: UnitOfWorkFactory

    async def execute(
        self,
        *,
        job_id: UUID,
    ) -> AnalysisJob:
        """Переводит active job в cancelled.

        Повторная отмена уже cancelled задания является идемпотентной.
        Completed и failed jobs остаются неизменными.
        """
        async with self.unit_of_work_factory() as unit_of_work:
            job = await unit_of_work.analysis_jobs.get_for_update(
                job_id,
            )

            if job is None:
                raise AnalysisJobCancellationNotFoundError(
                    f"Analysis job {job_id} not found.",
                )

            if job.status is AnalysisJobStatus.CANCELLED:
                return job

            if job.status not in {
                AnalysisJobStatus.PENDING,
                AnalysisJobStatus.QUEUED,
                AnalysisJobStatus.PROCESSING,
            }:
                raise AnalysisJobCancellationConflictError(
                    "Analysis job "
                    f"{job_id} cannot be cancelled "
                    f"from status {job.status.value}.",
                )

            job.mark_cancelled()

            await unit_of_work.analysis_jobs.update(
                job,
            )

            await unit_of_work.commit()

            return job
