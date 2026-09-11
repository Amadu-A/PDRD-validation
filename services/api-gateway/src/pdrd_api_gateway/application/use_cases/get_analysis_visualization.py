# services/api-gateway/src/pdrd_api_gateway/application/use_cases/get_analysis_visualization.py

"""Use case lazy-визуализации завершённого анализа."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pdrd_api_gateway.application.ports.analysis_visualization import (
    AnalysisFindingLocation,
    AnalysisFindingLocator,
    AnalysisFindingTarget,
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
    """Визуализация доступна только после completed."""


class AnalysisVisualizationUnavailableError(
    RuntimeError,
):
    """Не удалось подготовить visualization payload."""


@dataclass(frozen=True, slots=True)
class GetAnalysisVisualization:
    """Рендерит PDF и лениво локализует готовые findings."""

    get_analysis_job: GetAnalysisJob
    artifact_store: AnalysisArtifactStore
    pdf_page_renderer: AnalysisPdfPageRenderer
    finding_locator: AnalysisFindingLocator

    async def execute(
        self,
        *,
        job_id: UUID,
    ) -> dict[
        str,
        object,
    ]:
        """Возвращает PDF pages вместе с bbox locations."""
        job = await self.get_analysis_job.execute(
            job_id=job_id,
        )

        if job is None:
            raise AnalysisVisualizationJobNotFoundError(
                f"Analysis job {job_id} не найден.",
            )

        if job.status is not AnalysisJobStatus.COMPLETED:
            raise AnalysisVisualizationNotReadyError(
                "Визуализация анализа ещё не готова. "
                f"Текущий статус: {job.status.value}.",
            )

        if job.document_id is None:
            raise AnalysisVisualizationUnavailableError(
                "Completed analysis job не содержит document_id.",
            )

        try:
            artifacts = await self.artifact_store.load_request(
                document_id=job.document_id,
            )

            result = await self.artifact_store.load_result(
                document_id=job.document_id,
            )

        except Exception as error:
            raise AnalysisVisualizationUnavailableError(
                "Не удалось загрузить analysis artifacts.",
            ) from error

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

        if result is None:
            raise AnalysisVisualizationUnavailableError(
                "Completed analysis result отсутствует.",
            )

        try:
            pages = await self.pdf_page_renderer.render(
                pdf_content=artifacts.pdf_content,
                file_name=(artifacts.submission.pdf_file_name or "document.pdf"),
                page_spec=(artifacts.submission.pages),
            )

        except Exception as error:
            raise AnalysisVisualizationUnavailableError(
                "Не удалось подготовить PDF-preview.",
            ) from error

        raw_findings = result.get(
            "findings",
            [],
        )

        if not isinstance(
            raw_findings,
            list,
        ):
            raw_findings = []

        page_payloads: list[
            dict[
                str,
                object,
            ]
        ] = []

        for page in pages:
            targets = self._targets_for_page(
                findings=raw_findings,
                page_number=page.page_number,
            )

            localization_error = False

            if targets:
                try:
                    locations = await self.finding_locator.localize(
                        page_number=page.page_number,
                        extracted_text=(page.extracted_text),
                        image_base64=(page.image_base64),
                        findings=targets,
                    )

                except Exception:
                    locations = tuple(
                        AnalysisFindingLocation.unlocated(
                            finding_id=(target.finding_id),
                        )
                        for target in targets
                    )

                    localization_error = True

            else:
                locations = ()

            page_payload = page.as_dict()

            page_payload["locations"] = [location.as_dict() for location in locations]

            if localization_error:
                page_payload["localization_warning"] = (
                    "Точная visual localization временно недоступна."
                )

            page_payloads.append(
                page_payload,
            )

        return {
            "job_id": str(
                job_id,
            ),
            "document_id": str(
                job.document_id,
            ),
            "pages": page_payloads,
        }

    @classmethod
    def _targets_for_page(
        cls,
        *,
        findings: list[Any],
        page_number: int,
    ) -> tuple[
        AnalysisFindingTarget,
        ...,
    ]:
        """Выбирает findings конкретной physical PDF page."""
        targets: list[AnalysisFindingTarget] = []

        seen_ids: set[str] = set()

        for finding in findings:
            if not isinstance(
                finding,
                dict,
            ):
                continue

            finding_page = cls._normalize_page(
                finding.get(
                    "page",
                    finding.get(
                        "page_number",
                    ),
                )
            )

            if finding_page != page_number:
                continue

            finding_id = str(
                finding.get(
                    "finding_id",
                    "",
                )
            ).strip()

            if not finding_id or finding_id in seen_ids:
                continue

            seen_ids.add(
                finding_id,
            )

            targets.append(
                AnalysisFindingTarget(
                    finding_id=finding_id,
                    comment=str(
                        finding.get(
                            "comment",
                            "",
                        )
                    ),
                    evidence=str(
                        finding.get(
                            "evidence",
                            "",
                        )
                    ),
                )
            )

        return tuple(
            targets,
        )

    @staticmethod
    def _normalize_page(
        value: Any,
    ) -> int | None:
        """Нормализует physical page number."""
        if isinstance(
            value,
            bool,
        ):
            return None

        try:
            page = int(
                value,
            )

        except (
            TypeError,
            ValueError,
        ):
            return None

        if page < 1:
            return None

        return page
