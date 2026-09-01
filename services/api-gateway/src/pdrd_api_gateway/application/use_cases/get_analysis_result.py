# services/api-gateway/src/pdrd_api_gateway/application/use_cases/get_analysis_result.py

"""Use case получения готового результата анализа."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pdrd_api_gateway.application.ports.artifacts import (
    AnalysisArtifactStore,
)
from pdrd_api_gateway.application.use_cases.get_analysis_job import (
    GetAnalysisJob,
)
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJobStatus,
)


class AnalysisResultJobNotFoundError(LookupError):
    """Задание анализа не найдено."""


class AnalysisResultNotReadyError(RuntimeError):
    """Результат ещё не сформирован."""

    def __init__(
        self,
        *,
        status: AnalysisJobStatus,
    ) -> None:
        """Сохраняет текущее состояние job."""
        self.status = status

        super().__init__(
            f"Результат анализа ещё не готов. Текущий статус: {status.value}.",
        )


class AnalysisResultUnavailableError(RuntimeError):
    """Completed job не содержит доступного результата."""


@dataclass(frozen=True, slots=True)
class GetAnalysisResult:
    """Возвращает сохранённый JSON завершённого анализа."""

    get_analysis_job: GetAnalysisJob
    artifact_store: AnalysisArtifactStore

    async def execute(
        self,
        *,
        job_id: UUID,
    ) -> dict[str, Any]:
        """Возвращает результат completed job."""
        job = await self.get_analysis_job.execute(
            job_id=job_id,
        )

        if job is None:
            raise AnalysisResultJobNotFoundError(
                f"Analysis job {job_id} не найден.",
            )

        if job.status is not AnalysisJobStatus.COMPLETED:
            raise AnalysisResultNotReadyError(
                status=job.status,
            )

        if job.document_id is None:
            raise AnalysisResultUnavailableError(
                "Completed analysis job не содержит document_id.",
            )

        result = await self.artifact_store.load_result(
            document_id=job.document_id,
        )

        if result is None:
            raise AnalysisResultUnavailableError(
                "Analysis job имеет статус completed, но result.json отсутствует.",
            )

        return result
