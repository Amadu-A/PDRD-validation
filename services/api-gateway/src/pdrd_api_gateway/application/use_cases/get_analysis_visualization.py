# services/api-gateway/src/pdrd_api_gateway/application/use_cases/get_analysis_visualization.py

"""Use case lazy-визуализации завершённого анализа."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pdrd_api_gateway.application.finding_anchor_matcher import (
    FindingAnchorMatcher,
)
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
    """Рендерит PDF и гибридно локализует готовые findings."""

    get_analysis_job: GetAnalysisJob

    artifact_store: AnalysisArtifactStore

    pdf_page_renderer: AnalysisPdfPageRenderer

    finding_locator: AnalysisFindingLocator

    anchor_matcher: FindingAnchorMatcher

    async def execute(
        self,
        *,
        job_id: UUID,
    ) -> dict[
        str,
        object,
    ]:
        """Возвращает PDF pages вместе с exact/VLM locations."""
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
                document_id=(job.document_id),
            )

            result = await self.artifact_store.load_result(
                document_id=(job.document_id),
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
                pdf_content=(artifacts.pdf_content),
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
                page_number=(page.page_number),
            )

            deterministic_locations = self.anchor_matcher.locate(
                findings=targets,
                text_words=(page.text_words),
            )

            deterministic_by_id = {
                location.finding_id: location for location in deterministic_locations
            }

            unresolved_targets = tuple(
                target
                for target in targets
                if (deterministic_by_id[target.finding_id].status != "located")
            )

            fallback_by_id: dict[
                str,
                AnalysisFindingLocation,
            ] = {}

            localization_error = False

            if unresolved_targets:
                try:
                    fallback_locations = await self.finding_locator.localize(
                        page_number=(page.page_number),
                        extracted_text=(page.extracted_text),
                        image_base64=(page.image_base64),
                        findings=(unresolved_targets),
                    )

                    fallback_by_id = {
                        location.finding_id: location for location in fallback_locations
                    }

                except Exception:
                    localization_error = True

            locations = tuple(
                self._merge_location(
                    target=target,
                    deterministic=(deterministic_by_id[target.finding_id]),
                    fallback=(
                        fallback_by_id.get(
                            target.finding_id,
                        )
                    ),
                )
                for target in targets
            )

            page_payload = page.as_dict()

            page_payload["locations"] = [location.as_dict() for location in locations]

            page_payload["localization"] = self._localization_summary(
                locations,
            )

            if localization_error:
                page_payload["localization_warning"] = (
                    "Часть замечаний не удалось локализовать через VLM fallback."
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

    @staticmethod
    def _merge_location(
        *,
        target: AnalysisFindingTarget,
        deterministic: AnalysisFindingLocation,
        fallback: AnalysisFindingLocation | None,
    ) -> AnalysisFindingLocation:
        """Приоритетно сохраняет deterministic PDF location."""
        if deterministic.status == "located":
            return deterministic

        if fallback is not None and fallback.status == "located":
            return fallback

        return AnalysisFindingLocation.unlocated(
            finding_id=(target.finding_id),
        )

    @staticmethod
    def _localization_summary(
        locations: tuple[
            AnalysisFindingLocation,
            ...,
        ],
    ) -> dict[
        str,
        int,
    ]:
        """Возвращает диагностическую статистику страницы."""
        return {
            "pdf_text": sum(location.method == "pdf_text" for location in locations),
            "vlm": sum(location.method == "vlm" for location in locations),
            "unlocated": sum(location.status != "located" for location in locations),
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
