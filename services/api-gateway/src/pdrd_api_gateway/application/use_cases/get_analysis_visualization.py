# services/api-gateway/src/pdrd_api_gateway/application/use_cases/get_analysis_visualization.py

"""Use case lazy-визуализации завершённого анализа."""

import logging
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
    AnalysisPagePreview,
    AnalysisPdfPageRenderer,
)
from pdrd_api_gateway.application.ports.analysis_visualization_cache import (
    AnalysisVisualizationCache,
    AnalysisVisualizationCacheError,
    AnalysisVisualizationLocationPage,
)
from pdrd_api_gateway.application.ports.artifacts import (
    AnalysisArtifactStorageError,
    AnalysisArtifactStore,
)
from pdrd_api_gateway.application.use_cases.get_analysis_job import (
    GetAnalysisJob,
)
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJobStatus,
)

logger = logging.getLogger(
    "uvicorn.error",
)

_VISUAL_REFINEMENT_MARKERS = (
    "не содержит",
    "отсутств",
    "не показ",
    "не изображ",
    "не нанес",
    "не обознач",
    "не указан",
    "не предусмотр",
    "не установлен",
    "не выполнен",
    "не выполнена",
    "не выполнено",
    "не подключ",
    "не прослеж",
    "разрыв",
    "missing",
    "absent",
    "not shown",
    "not provided",
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
    """Читает reusable preview и гибридно локализует готовые findings."""

    get_analysis_job: GetAnalysisJob

    artifact_store: AnalysisArtifactStore

    pdf_page_renderer: AnalysisPdfPageRenderer

    finding_locator: AnalysisFindingLocator

    anchor_matcher: FindingAnchorMatcher

    visualization_cache: AnalysisVisualizationCache | None = None

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

        pages = await self._load_or_render_pages(
            document_id=job.document_id,
            pdf_content=artifacts.pdf_content,
            file_name=(artifacts.submission.pdf_file_name or "document.pdf"),
            page_spec=artifacts.submission.pages,
        )

        raw_findings = result.get(
            "findings",
            [],
        )

        if not isinstance(
            raw_findings,
            list,
        ):
            raw_findings = []

        cached_pages = await self._load_cached_locations(
            document_id=job.document_id,
        )

        cached_by_page = {page.page_number: page for page in cached_pages}

        cache_pages: list[AnalysisVisualizationLocationPage] = []

        cache_changed = False

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

            cached_page = cached_by_page.get(
                page.page_number,
            )

            localization_error = False

            if self._cached_page_matches(
                cached_page=cached_page,
                targets=targets,
            ):
                assert cached_page is not None

                locations = cached_page.locations

                cache_pages.append(
                    cached_page,
                )

            else:
                cache_changed = True

                deterministic_locations = self.anchor_matcher.locate(
                    findings=targets,
                    text_words=page.text_words,
                )

                deterministic_by_id = {
                    location.finding_id: location
                    for location in deterministic_locations
                }

                unresolved_targets = tuple(
                    target
                    for target in targets
                    if (deterministic_by_id[target.finding_id].status != "located")
                )

                refinement_targets = tuple(
                    target
                    for target in targets
                    if self._requires_visual_refinement(
                        target,
                    )
                )

                vlm_target_ids = {
                    target.finding_id
                    for target in (
                        *unresolved_targets,
                        *refinement_targets,
                    )
                }

                vlm_targets = tuple(
                    target
                    for target in targets
                    if (target.finding_id in vlm_target_ids)
                )

                fallback_by_id: dict[
                    str,
                    AnalysisFindingLocation,
                ] = {}

                if vlm_targets:
                    try:
                        fallback_locations = await self.finding_locator.localize(
                            page_number=page.page_number,
                            extracted_text=page.extracted_text,
                            image_base64=page.image_base64,
                            findings=vlm_targets,
                        )

                        fallback_by_id = {
                            location.finding_id: location
                            for location in fallback_locations
                        }

                    except Exception:
                        localization_error = True

                refinement_ids = {target.finding_id for target in refinement_targets}

                locations = tuple(
                    self._merge_location(
                        target=target,
                        deterministic=(deterministic_by_id[target.finding_id]),
                        fallback=(
                            fallback_by_id.get(
                                target.finding_id,
                            )
                        ),
                        prefer_vlm=(target.finding_id in refinement_ids),
                    )
                    for target in targets
                )

                if not localization_error:
                    cache_pages.append(
                        AnalysisVisualizationLocationPage(
                            page_number=page.page_number,
                            locations=locations,
                        )
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

        if cache_changed and cache_pages:
            await self._save_cached_locations(
                document_id=job.document_id,
                pages=tuple(
                    cache_pages,
                ),
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

    async def _load_or_render_pages(
        self,
        *,
        document_id: UUID,
        pdf_content: bytes,
        file_name: str,
        page_spec: str | None,
    ) -> tuple[
        AnalysisPagePreview,
        ...,
    ]:
        """Читает Stage 7 artifact, а для legacy/corrupt job делает fallback."""
        try:
            pages = await self.artifact_store.load_visualization(
                document_id=document_id,
            )

        except AnalysisArtifactStorageError as error:
            logger.warning(
                (
                    "analysis_visualization_artifact "
                    "cache_hit=false corrupt=true "
                    "document_id=%s error=%s"
                ),
                document_id,
                error,
            )

            pages = None

        if pages is not None:
            logger.info(
                (
                    "analysis_visualization_artifact "
                    "cache_hit=true "
                    "document_id=%s pages=%s"
                ),
                document_id,
                len(
                    pages,
                ),
            )

            return pages

        try:
            pages = await self.pdf_page_renderer.render(
                pdf_content=pdf_content,
                file_name=file_name,
                page_spec=page_spec,
            )

        except Exception as error:
            raise AnalysisVisualizationUnavailableError(
                "Не удалось подготовить PDF-preview.",
            ) from error

        try:
            await self.artifact_store.save_visualization(
                document_id=document_id,
                pages=pages,
            )

        except AnalysisArtifactStorageError as error:
            logger.warning(
                (
                    "analysis_visualization_artifact "
                    "fallback_persisted=false "
                    "document_id=%s error=%s"
                ),
                document_id,
                error,
            )

        else:
            logger.info(
                (
                    "analysis_visualization_artifact "
                    "fallback_persisted=true "
                    "document_id=%s pages=%s"
                ),
                document_id,
                len(
                    pages,
                ),
            )

        logger.info(
            ("analysis_visualization_artifact cache_hit=false document_id=%s pages=%s"),
            document_id,
            len(
                pages,
            ),
        )

        return pages

    async def _load_cached_locations(
        self,
        *,
        document_id: UUID,
    ) -> tuple[
        AnalysisVisualizationLocationPage,
        ...,
    ]:
        """Best-effort читает reusable finding location cache."""
        if self.visualization_cache is None:
            return ()

        try:
            pages = await self.visualization_cache.load_locations(
                document_id=document_id,
            )

        except AnalysisVisualizationCacheError as error:
            logger.warning(
                (
                    "analysis_location_cache "
                    "cache_hit=false corrupt=true "
                    "document_id=%s error=%s"
                ),
                document_id,
                error,
            )

            return ()

        if pages is None:
            return ()

        logger.info(
            ("analysis_location_cache cache_hit=true document_id=%s pages=%s"),
            document_id,
            len(
                pages,
            ),
        )

        return pages

    async def _save_cached_locations(
        self,
        *,
        document_id: UUID,
        pages: tuple[
            AnalysisVisualizationLocationPage,
            ...,
        ],
    ) -> None:
        """Best-effort сохраняет reusable locations."""
        if self.visualization_cache is None:
            return

        try:
            await self.visualization_cache.save_locations(
                document_id=document_id,
                pages=pages,
            )

        except AnalysisVisualizationCacheError as error:
            logger.warning(
                ("analysis_location_cache persisted=false document_id=%s error=%s"),
                document_id,
                error,
            )

    @staticmethod
    def _cached_page_matches(
        *,
        cached_page: (AnalysisVisualizationLocationPage | None),
        targets: tuple[
            AnalysisFindingTarget,
            ...,
        ],
    ) -> bool:
        """Проверяет, что cache соответствует текущему ordered finding feed."""
        if cached_page is None:
            return False

        cached_ids = tuple(location.finding_id for location in cached_page.locations)

        expected_ids = tuple(target.finding_id for target in targets)

        return cached_ids == expected_ids

    @staticmethod
    def _merge_location(
        *,
        target: AnalysisFindingTarget,
        deterministic: AnalysisFindingLocation,
        fallback: AnalysisFindingLocation | None,
        prefer_vlm: bool = False,
    ) -> AnalysisFindingLocation:
        """Объединяет PDF geometry и VLM refinement без потери safe fallback."""
        if prefer_vlm and fallback is not None and fallback.status == "located":
            return fallback

        if deterministic.status == "located":
            return deterministic

        if fallback is not None and fallback.status == "located":
            return fallback

        return AnalysisFindingLocation.unlocated(
            finding_id=target.finding_id,
        )

    @staticmethod
    def _requires_visual_refinement(
        target: AnalysisFindingTarget,
    ) -> bool:
        """Определяет findings, где нужно локализовать evidence area отсутствия."""
        finding_text = "\n".join(
            (
                target.comment,
                target.evidence,
            )
        ).casefold()

        return any(marker in finding_text for marker in _VISUAL_REFINEMENT_MARKERS)

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
