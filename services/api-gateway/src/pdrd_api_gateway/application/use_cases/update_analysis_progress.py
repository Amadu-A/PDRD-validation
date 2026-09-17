# services/api-gateway/src/pdrd_api_gateway/application/use_cases/update_analysis_progress.py

"""Use case durable progress analysis job."""

from dataclasses import dataclass
from uuid import UUID

from pdrd_api_gateway.application.ports.persistence import (
    UnitOfWorkFactory,
)
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisProgressStage,
)


class AnalysisProgressJobNotFoundError(
    LookupError,
):
    """Progress callback относится к неизвестному document_id."""


@dataclass(frozen=True, slots=True)
class UpdateAnalysisProgress:
    """Записывает monotonic progress callback из orchestration."""

    unit_of_work_factory: UnitOfWorkFactory

    async def execute(
        self,
        *,
        document_id: UUID,
        stage: AnalysisProgressStage,
    ) -> bool:
        """Сохраняет stage и возвращает признак фактического изменения."""
        async with self.unit_of_work_factory() as unit_of_work:
            job = await unit_of_work.analysis_jobs.get_by_document_id_for_update(
                document_id,
            )

            if job is None:
                raise AnalysisProgressJobNotFoundError(
                    f"Analysis job для document_id={document_id} не найден.",
                )

            changed = job.update_progress(
                stage=stage,
            )

            if not changed:
                return False

            await unit_of_work.analysis_jobs.update(
                job,
            )

            await unit_of_work.commit()

            return True
