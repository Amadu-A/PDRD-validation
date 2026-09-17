# services/api-gateway/src/pdrd_api_gateway/application/use_cases/update_analysis_progress.py

"""Use case durable progress analysis job."""

from dataclasses import dataclass
from uuid import UUID

from pdrd_api_gateway.application.ports.persistence import (
    UnitOfWorkFactory,
)
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJobStatus,
    AnalysisProgressStage,
)


class AnalysisProgressJobNotFoundError(
    LookupError,
):
    """Progress callback относится к неизвестному document_id."""


@dataclass(frozen=True, slots=True)
class AnalysisProgressUpdateResult:
    """Результат одного durable progress checkpoint."""

    changed: bool

    cancelled: bool


@dataclass(frozen=True, slots=True)
class UpdateAnalysisProgress:
    """Записывает monotonic progress callback из orchestration."""

    unit_of_work_factory: UnitOfWorkFactory

    async def execute(
        self,
        *,
        document_id: UUID,
        stage: AnalysisProgressStage,
    ) -> AnalysisProgressUpdateResult:
        """Сохраняет stage и сообщает orchestration о durable cancellation."""
        async with self.unit_of_work_factory() as unit_of_work:
            job = await unit_of_work.analysis_jobs.get_by_document_id_for_update(
                document_id,
            )

            if job is None:
                raise AnalysisProgressJobNotFoundError(
                    f"Analysis job для document_id={document_id} не найден.",
                )

            if job.status is AnalysisJobStatus.CANCELLED:
                return AnalysisProgressUpdateResult(
                    changed=False,
                    cancelled=True,
                )

            changed = job.update_progress(
                stage=stage,
            )

            if changed:
                await unit_of_work.analysis_jobs.update(
                    job,
                )

                await unit_of_work.commit()

            return AnalysisProgressUpdateResult(
                changed=changed,
                cancelled=False,
            )
