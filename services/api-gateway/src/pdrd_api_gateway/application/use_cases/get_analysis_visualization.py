# services/api-gateway/src/pdrd_api_gateway/application/use_cases/get_analysis_visualization.py

"""Use case выдачи визуальных страниц завершённого анализа."""

from dataclasses import dataclass
from uuid import UUID

from pdrd_api_gateway.application.ports.analysis_visualization import (
    AnalysisPdfPageRenderer,
)
from pdrd_api_gateway.application.ports.artifacts import (
    AnalysisArtifactStore,
)
from pdrd_api_gateway.application.use_cases.get_analysis_job import (
    GetAnalysisJob,
)
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJobStatus,
)


class AnalysisVisualizationJobNotFoundError(
    LookupError,
):
    """Задание анализа не найдено."""


class AnalysisVisualizationNotReadyError(
    RuntimeError,
):
    """Визуализацию можно строить только для completed job."""

    def __init__(
        self,
        *,
        status: AnalysisJobStatus,
    ) -> None:
        """Сохраняет текущий status."""
        self.status = status

        super().__init__(
            f"Визуализация анализа ещё не готова. Текущий статус: {status.value}.",
        )


class AnalysisVisualizationUnavailableError(
    RuntimeError,
):
    """Не удалось подготовить preview исходного PDF."""


@dataclass(frozen=True, slots=True)
class GetAnalysisVisualization:
    """Повторно рендерит только выбранные страницы исходного PDF."""

    get_analysis_job: GetAnalysisJob
    artifact_store: AnalysisArtifactStore
    pdf_page_renderer: AnalysisPdfPageRenderer

    async def execute(
        self,
        *,
        job_id: UUID,
    ) -> dict[
        str,
        object,
    ]:
        """Возвращает PNG-preview без раздувания persisted result.json."""
        job = await self.get_analysis_job.execute(
            job_id=job_id,
        )

        if job is None:
            raise AnalysisVisualizationJobNotFoundError(
                f"Analysis job {job_id} не найден.",
            )

        if job.status is not AnalysisJobStatus.COMPLETED:
            raise AnalysisVisualizationNotReadyError(
                status=job.status,
            )

        if job.document_id is None:
            raise AnalysisVisualizationUnavailableError(
                "Completed analysis job не содержит document_id.",
            )

        artifacts = await self.artifact_store.load_request(
            document_id=job.document_id,
        )

        if artifacts.pdf_content is None:
            return {
                "job_id": str(
                    job_id,
                ),
                "document_id": str(
                    job.document_id,
                ),
                "pages": [],
            }

        submission = artifacts.submission

        try:
            pages = await self.pdf_page_renderer.render(
                pdf_content=artifacts.pdf_content,
                file_name=(submission.pdf_file_name or "document.pdf"),
                page_spec=submission.pages,
            )

        except Exception as error:
            raise AnalysisVisualizationUnavailableError(
                "Не удалось подготовить PDF-preview для визуализации: "
                f"{type(error).__name__}: {error}",
            ) from error

        return {
            "job_id": str(
                job_id,
            ),
            "document_id": str(
                job.document_id,
            ),
            "pages": [page.as_dict() for page in pages],
        }
