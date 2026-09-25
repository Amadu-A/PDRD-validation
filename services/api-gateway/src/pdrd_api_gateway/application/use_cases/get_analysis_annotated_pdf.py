# services/api-gateway/src/pdrd_api_gateway/application/use_cases/get_analysis_annotated_pdf.py

"""Use case скачиваемого PDF с native annotations и текстовым отчётом."""

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from pdrd_api_gateway.application.ports.analysis_pdf_export import (
    AnalysisAnnotatedPdfDocument,
    AnalysisAnnotatedPdfRenderer,
    AnalysisPdfAnnotation,
    AnalysisPdfReport,
    AnalysisPdfReportField,
    AnalysisPdfReportFinding,
)
from pdrd_api_gateway.application.ports.analysis_visualization import (
    AnalysisBoundingBox,
)
from pdrd_api_gateway.application.ports.analysis_visualization_cache import (
    AnalysisVisualizationCache,
    AnalysisVisualizationCacheError,
)
from pdrd_api_gateway.application.ports.artifacts import (
    AnalysisArtifactStore,
)
from pdrd_api_gateway.application.use_cases.get_analysis_job import (
    GetAnalysisJob,
)
from pdrd_api_gateway.application.use_cases.get_analysis_visualization import (
    GetAnalysisVisualization,
)
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJobStatus,
)

logger = logging.getLogger(
    "uvicorn.error",
)

_STATUS_LABELS = {
    "pending": "Заявка принята",
    "queued": "Ожидает обработки",
    "processing": "Выполняется анализ",
    "completed": "Анализ завершён",
    "failed": "Ошибка анализа",
    "cancelled": "Анализ отменён",
    "confirmed": "Подтверждено",
    "needs_review": "Требует проверки инженером",
}

_SEVERITY_LABELS = {
    "info": "Информация",
    "warning": "Предупреждение",
    "error": "Ошибка",
}

_CATEGORY_LABELS = {
    "normative_control": "Нормоконтроль",
    "equipment": "Оборудование",
    "scheme_logic": "Логика схемы",
    "marking": "Маркировка",
    "completeness": "Комплектность",
    "optimization": "Оптимизация",
    "customer_requirements": "Требования заказчика",
    "other": "Прочее",
}

_SOURCE_MODE_LABELS = {
    "pdf_only": "PDF",
    "cad_only": "DWG/DXF",
    "pdf_cad": "PDF + DWG/DXF",
}

_GENERIC_FINDING_PREFIX = "на листе выявлено несоответствие"


class AnalysisAnnotatedPdfJobNotFoundError(
    LookupError,
):
    """Analysis job не найден."""


class AnalysisAnnotatedPdfNotReadyError(
    RuntimeError,
):
    """PDF export доступен только для completed job."""


class AnalysisAnnotatedPdfSourceUnavailableError(
    RuntimeError,
):
    """У analysis job нет исходного PDF."""


class AnalysisAnnotatedPdfUnavailableError(
    RuntimeError,
):
    """Не удалось сформировать annotated PDF."""


@dataclass(frozen=True, slots=True)
class _LocatedFinding:
    """Resolved PDF location одного finding."""

    page_number: int

    regions: tuple[
        AnalysisBoundingBox,
        ...,
    ]


@dataclass(frozen=True, slots=True)
class GetAnalysisAnnotatedPdf:
    """Формирует или читает cached annotated PDF completed analysis."""

    get_analysis_job: GetAnalysisJob

    artifact_store: AnalysisArtifactStore

    get_analysis_visualization: GetAnalysisVisualization

    renderer: AnalysisAnnotatedPdfRenderer

    visualization_cache: AnalysisVisualizationCache

    async def execute(
        self,
        *,
        job_id: UUID,
    ) -> AnalysisAnnotatedPdfDocument:
        """Возвращает downloadable PDF с annotations и полным report."""
        job = await self.get_analysis_job.execute(
            job_id=job_id,
        )

        if job is None:
            raise AnalysisAnnotatedPdfJobNotFoundError(
                f"Analysis job {job_id} не найден.",
            )

        if job.status is not AnalysisJobStatus.COMPLETED:
            raise AnalysisAnnotatedPdfNotReadyError(
                "Annotated PDF доступен только "
                "после завершения анализа. "
                f"Текущий статус: {job.status.value}.",
            )

        if job.document_id is None:
            raise AnalysisAnnotatedPdfUnavailableError(
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
            raise AnalysisAnnotatedPdfUnavailableError(
                "Не удалось загрузить analysis artifacts.",
            ) from error

        if artifacts.pdf_content is None:
            raise AnalysisAnnotatedPdfSourceUnavailableError(
                ("Для этого analysis job исходный PDF отсутствует."),
            )

        if result is None:
            raise AnalysisAnnotatedPdfUnavailableError(
                "Completed analysis result отсутствует.",
            )

        file_name = self._output_file_name(
            (artifacts.submission.pdf_file_name or "document.pdf"),
        )

        try:
            cached_pdf = await self.visualization_cache.load_annotated_pdf(
                document_id=job.document_id,
            )

        except AnalysisVisualizationCacheError as error:
            logger.warning(
                (
                    "analysis_annotated_pdf "
                    "cache_hit=false corrupt=true "
                    "document_id=%s error=%s"
                ),
                job.document_id,
                error,
            )

            cached_pdf = None

        if cached_pdf is not None:
            logger.info(
                ("analysis_annotated_pdf cache_hit=true document_id=%s"),
                job.document_id,
            )

            return AnalysisAnnotatedPdfDocument(
                content=cached_pdf,
                file_name=file_name,
            )

        try:
            visualization = await self.get_analysis_visualization.execute(
                job_id=job_id,
            )

        except Exception as error:
            raise AnalysisAnnotatedPdfUnavailableError(
                ("Не удалось получить locations для PDF export."),
            ) from error

        (
            annotations,
            report,
        ) = self._build_export_payload(
            job_id=job_id,
            result=result,
            visualization=visualization,
            source_mode=(artifacts.submission.source_mode.value),
            pdf_file_name=(artifacts.submission.pdf_file_name),
            cad_file_name=(artifacts.submission.cad_file_name),
        )

        try:
            content = await self.renderer.render(
                pdf_content=artifacts.pdf_content,
                file_name=(artifacts.submission.pdf_file_name or "document.pdf"),
                annotations=annotations,
                report=report,
            )

        except Exception as error:
            raise AnalysisAnnotatedPdfUnavailableError(
                ("Document Service не сформировал annotated PDF."),
            ) from error

        if not content.startswith(
            b"%PDF-",
        ):
            raise AnalysisAnnotatedPdfUnavailableError(
                "Annotated artifact не является PDF.",
            )

        try:
            await self.visualization_cache.save_annotated_pdf(
                document_id=job.document_id,
                content=content,
            )

        except AnalysisVisualizationCacheError as error:
            logger.warning(
                ("analysis_annotated_pdf persisted=false document_id=%s error=%s"),
                job.document_id,
                error,
            )

        logger.info(
            (
                "analysis_annotated_pdf "
                "cache_hit=false generated=true "
                "document_id=%s annotations=%s"
            ),
            job.document_id,
            len(
                annotations,
            ),
        )

        return AnalysisAnnotatedPdfDocument(
            content=content,
            file_name=file_name,
        )

    @classmethod
    def _build_export_payload(
        cls,
        *,
        job_id: UUID,
        result: dict[
            str,
            Any,
        ],
        visualization: dict[
            str,
            object,
        ],
        source_mode: str,
        pdf_file_name: str | None,
        cad_file_name: str | None,
    ) -> tuple[
        tuple[
            AnalysisPdfAnnotation,
            ...,
        ],
        AnalysisPdfReport,
    ]:
        """Строит annotation feed и текстовый report без потери findings."""
        locations = cls._locations_by_finding(
            visualization,
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

        default_page = cls._default_page(
            result,
        )

        annotations: list[AnalysisPdfAnnotation] = []

        report_findings: list[AnalysisPdfReportFinding] = []

        valid_findings = [
            finding
            for finding in raw_findings
            if isinstance(
                finding,
                dict,
            )
            and finding.get("status") != "hypothesis"
        ]

        for index, finding in enumerate(
            valid_findings,
            start=1,
        ):
            page_number = (
                cls._normalize_page(
                    finding.get(
                        "page",
                        finding.get(
                            "page_number",
                        ),
                    )
                )
                or default_page
            )

            finding_id = str(
                finding.get(
                    "finding_id",
                    "",
                )
            ).strip()

            located = (
                locations.get(
                    finding_id,
                )
                if finding_id
                else None
            )

            fields = cls._finding_fields(
                finding=finding,
                located=located,
                page_number=page_number,
            )

            report_findings.append(
                AnalysisPdfReportFinding(
                    title=(f"{index}. Лист/страница {page_number}"),
                    fields=fields,
                )
            )

            annotation_finding_id = finding_id or f"finding-{index}"

            annotation_page_number = (
                located.page_number if located is not None else page_number
            )

            annotation_regions = located.regions if located is not None else ()

            annotations.append(
                AnalysisPdfAnnotation(
                    number=index,
                    finding_id=annotation_finding_id,
                    page_number=annotation_page_number,
                    title=cls._annotation_card_text(
                        finding,
                    ),
                    content=cls._annotation_content(
                        fields,
                    ),
                    regions=annotation_regions,
                )
            )

        metadata = cls._report_metadata(
            job_id=job_id,
            result=result,
            source_mode=source_mode,
            pdf_file_name=pdf_file_name,
            cad_file_name=cad_file_name,
            findings_count=len(
                valid_findings,
            ),
        )

        limitations = result.get(
            "limitations",
            [],
        )

        if not isinstance(
            limitations,
            list,
        ):
            limitations = []

        report = AnalysisPdfReport(
            title="Отчёт анализа PDRD",
            metadata=metadata,
            summary=str(
                result.get(
                    "summary",
                    "",
                )
                or ""
            ),
            findings=tuple(
                report_findings,
            ),
            limitations=tuple(
                str(
                    limitation,
                )
                for limitation in limitations
                if str(
                    limitation,
                ).strip()
            ),
        )

        return (
            tuple(
                annotations,
            ),
            report,
        )

    @classmethod
    def _annotation_card_text(
        cls,
        finding: dict[
            str,
            Any,
        ],
    ) -> str:
        """Строит compact card text для native PDF callout."""
        summary = cls._finding_display_text(
            finding,
        )

        source_label = cls._compact_normative_source_label(
            finding,
        )

        lines = [
            cls._shorten_text(
                summary,
                limit=220,
            ),
        ]

        if source_label:
            lines.append(
                "Норматив: "
                + cls._shorten_text(
                    source_label,
                    limit=150,
                )
            )

        return "\n".join(line for line in lines if line)

    @classmethod
    def _finding_display_text(
        cls,
        finding: dict[
            str,
            Any,
        ],
    ) -> str:
        """Выбирает содержательный compact текст вместо generic comment."""
        comment = str(
            finding.get(
                "comment",
            )
            or finding.get(
                "message",
            )
            or finding.get(
                "issue_text",
            )
            or ""
        ).strip()

        evidence = str(
            finding.get(
                "evidence",
                "",
            )
            or ""
        ).strip()

        normalized_comment = " ".join(comment.casefold().split())

        if evidence and normalized_comment.startswith(_GENERIC_FINDING_PREFIX):
            return evidence

        return comment or evidence or "Текст замечания не передан."

    @classmethod
    def _compact_normative_source_label(
        cls,
        finding: dict[
            str,
            Any,
        ],
    ) -> str:
        """Возвращает короткую подпись первого N-source для callout."""
        sources = cls._preferred_sources(
            finding,
            "normative_sources",
            "basis_sources",
        )

        for source in sources:
            if not isinstance(
                source,
                dict,
            ):
                continue

            source_name = str(
                source.get(
                    "source_file",
                )
                or source.get(
                    "file_name",
                )
                or source.get(
                    "source",
                )
                or source.get(
                    "source_id",
                )
                or "Нормативный источник"
            ).strip()

            page = cls._normalize_page(
                source.get(
                    "page",
                    source.get(
                        "page_number",
                    ),
                )
            )

            if page is not None:
                return f"{source_name}, стр. {page}"

            return source_name

        return ""

    @staticmethod
    def _shorten_text(
        value: str,
        *,
        limit: int,
    ) -> str:
        """Ограничивает только visible callout text, не popup/report."""
        normalized = " ".join(
            str(
                value,
            ).split()
        )

        if (
            len(
                normalized,
            )
            <= limit
        ):
            return normalized

        return (
            normalized[
                : max(
                    0,
                    limit - 1,
                )
            ].rstrip()
            + "…"
        )

    @classmethod
    def _finding_fields(
        cls,
        *,
        finding: dict[
            str,
            Any,
        ],
        located: _LocatedFinding | None,
        page_number: int,
    ) -> tuple[
        AnalysisPdfReportField,
        ...,
    ]:
        """Формирует поля так же раздельно, как browser report."""
        fields: list[AnalysisPdfReportField] = []

        cls._append_field(
            fields,
            "Статус",
            _STATUS_LABELS.get(
                str(
                    finding.get(
                        "status",
                        "",
                    )
                ),
                str(
                    finding.get(
                        "status",
                        "",
                    )
                    or "Не определён"
                ),
            ),
        )

        severity = str(
            finding.get(
                "severity",
                "",
            )
        )

        cls._append_field(
            fields,
            "Уровень",
            _SEVERITY_LABELS.get(
                severity,
                severity or "Уровень не указан",
            ),
        )

        category = str(
            finding.get(
                "category",
                "",
            )
        )

        cls._append_field(
            fields,
            "Категория",
            _CATEGORY_LABELS.get(
                category,
                category or "—",
            ),
        )

        cls._append_field(
            fields,
            "Замечание",
            (
                finding.get(
                    "comment",
                )
                or finding.get(
                    "message",
                )
                or finding.get(
                    "issue_text",
                )
                or "Текст замечания не передан."
            ),
        )

        cls._append_field(
            fields,
            "Основание на листе",
            finding.get(
                "evidence",
            ),
        )

        cls._append_field(
            fields,
            "Нормативное основание",
            finding.get(
                "basis",
            ),
        )

        cls._append_field(
            fields,
            "Нормативные источники",
            cls._source_value(
                cls._preferred_sources(
                    finding,
                    "normative_sources",
                    "basis_sources",
                )
            ),
        )

        cls._append_field(
            fields,
            "Требования технического задания",
            cls._source_value(
                cls._preferred_sources(
                    finding,
                    ("technical_assignment_basis_sources"),
                    ("technical_assignment_sources"),
                )
            ),
        )

        cls._append_field(
            fields,
            ("Пользовательские требования / документы"),
            cls._source_value(
                cls._preferred_sources(
                    finding,
                    ("user_package_basis_sources"),
                    "user_package_sources",
                )
            ),
        )

        cls._append_field(
            fields,
            "Контекст ПЗ",
            cls._source_value(
                finding.get(
                    "project_context_sources",
                    [],
                ),
            ),
        )

        cls._append_field(
            fields,
            "Рекомендация",
            (
                finding.get(
                    "recommendation",
                )
                or finding.get(
                    "recommendation_draft",
                )
                or "Не указана."
            ),
        )

        confidence = finding.get(
            "confidence",
        )

        if confidence is not None:
            cls._append_field(
                fields,
                "Уверенность",
                confidence,
            )

        if located is None:
            localization = (
                "Точное место автоматически "
                "не локализовано. "
                "PDF-аннотация размещена "
                "на уровне "
                f"листа {page_number}."
            )

        else:
            localization = (
                "PDF-аннотация размещена "
                "по найденной области "
                f"на листе {located.page_number}."
            )

        cls._append_field(
            fields,
            "Локализация",
            localization,
        )

        return tuple(
            fields,
        )

    @classmethod
    def _report_metadata(
        cls,
        *,
        job_id: UUID,
        result: dict[
            str,
            Any,
        ],
        source_mode: str,
        pdf_file_name: str | None,
        cad_file_name: str | None,
        findings_count: int,
    ) -> tuple[
        AnalysisPdfReportField,
        ...,
    ]:
        """Строит сводку анализа для appended report."""
        fields: list[AnalysisPdfReportField] = []

        cls._append_field(
            fields,
            "Задание",
            job_id,
        )

        cls._append_field(
            fields,
            "Режим",
            _SOURCE_MODE_LABELS.get(
                source_mode,
                source_mode,
            ),
        )

        cls._append_field(
            fields,
            "PDF",
            pdf_file_name,
        )

        cls._append_field(
            fields,
            "DWG/DXF",
            cad_file_name,
        )

        selected_pages = result.get(
            "selected_pages",
        )

        if (
            isinstance(
                selected_pages,
                list,
            )
            and selected_pages
        ):
            cls._append_field(
                fields,
                "Страницы",
                ", ".join(
                    str(
                        page,
                    )
                    for page in selected_pages
                ),
            )

        cls._append_field(
            fields,
            "Проанализировано листов",
            result.get(
                "analyzed_pages",
            ),
        )

        cls._append_field(
            fields,
            "Замечаний",
            findings_count,
        )

        pipeline = result.get(
            "pipeline",
        )

        if (
            isinstance(
                pipeline,
                list,
            )
            and pipeline
        ):
            cls._append_field(
                fields,
                "Pipeline",
                " → ".join(
                    str(
                        stage,
                    )
                    for stage in pipeline
                ),
            )

        context = result.get(
            "explanatory_note_context",
        )

        if isinstance(
            context,
            dict,
        ):
            if context.get(
                "enabled",
            ):
                cls._append_field(
                    fields,
                    "Контекст ПЗ",
                    "Включён",
                )

                start_page = context.get(
                    "start_page",
                )

                end_page = context.get(
                    "end_page",
                )

                if start_page and end_page:
                    cls._append_field(
                        fields,
                        "Диапазон ПЗ",
                        (f"{start_page}-{end_page}"),
                    )

                cls._append_field(
                    fields,
                    "Страниц ПЗ",
                    context.get(
                        "pages_count",
                    ),
                )

            else:
                cls._append_field(
                    fields,
                    "Контекст ПЗ",
                    "Выключен",
                )

        return tuple(
            fields,
        )

    @classmethod
    def _locations_by_finding(
        cls,
        visualization: dict[
            str,
            object,
        ],
    ) -> dict[
        str,
        _LocatedFinding,
    ]:
        """Извлекает только реально located bbox из visualization payload."""
        result: dict[
            str,
            _LocatedFinding,
        ] = {}

        raw_pages = visualization.get(
            "pages",
            [],
        )

        if not isinstance(
            raw_pages,
            list,
        ):
            return result

        for raw_page in raw_pages:
            if not isinstance(
                raw_page,
                dict,
            ):
                continue

            page_number = cls._normalize_page(
                raw_page.get(
                    "page_number",
                )
            )

            if page_number is None:
                continue

            raw_locations = raw_page.get(
                "locations",
                [],
            )

            if not isinstance(
                raw_locations,
                list,
            ):
                continue

            for raw_location in raw_locations:
                if not isinstance(
                    raw_location,
                    dict,
                ):
                    continue

                if (
                    raw_location.get(
                        "status",
                    )
                    != "located"
                ):
                    continue

                finding_id = str(
                    raw_location.get(
                        "finding_id",
                        "",
                    )
                ).strip()

                if not finding_id:
                    continue

                regions = cls._location_regions(
                    raw_location,
                )

                if not regions:
                    continue

                result[finding_id] = _LocatedFinding(
                    page_number=page_number,
                    regions=regions,
                )

        return result

    @staticmethod
    def _location_regions(
        raw_location: dict[
            str,
            Any,
        ],
    ) -> tuple[
        AnalysisBoundingBox,
        ...,
    ]:
        """Парсит multi-region bbox с backward-compatible primary bbox."""
        raw_regions = raw_location.get(
            "regions",
        )

        if not isinstance(
            raw_regions,
            list,
        ):
            raw_regions = []

        bbox_payloads: list[object] = []

        for raw_region in raw_regions:
            if not isinstance(
                raw_region,
                dict,
            ):
                continue

            bbox_payloads.append(
                raw_region.get(
                    "bbox",
                )
            )

        if not bbox_payloads:
            bbox_payloads.append(
                raw_location.get(
                    "bbox",
                )
            )

        regions: list[AnalysisBoundingBox] = []

        for raw_bbox in bbox_payloads:
            if not isinstance(
                raw_bbox,
                dict,
            ):
                continue

            try:
                regions.append(
                    AnalysisBoundingBox(
                        x_min=int(raw_bbox["x_min"]),
                        y_min=int(raw_bbox["y_min"]),
                        x_max=int(raw_bbox["x_max"]),
                        y_max=int(raw_bbox["y_max"]),
                    )
                )

            except (
                KeyError,
                TypeError,
                ValueError,
            ):
                continue

        return tuple(
            regions,
        )

    @staticmethod
    def _preferred_sources(
        finding: dict[
            str,
            Any,
        ],
        primary: str,
        fallback: str,
    ) -> list[Any]:
        """Выбирает primary typed source bucket без смешивания N/T/U."""
        primary_value = finding.get(
            primary,
        )

        if (
            isinstance(
                primary_value,
                list,
            )
            and primary_value
        ):
            return primary_value

        fallback_value = finding.get(
            fallback,
        )

        if isinstance(
            fallback_value,
            list,
        ):
            return fallback_value

        return []

    @classmethod
    def _source_value(
        cls,
        sources: object,
    ) -> str:
        """Формирует текстовое представление source list."""
        if not isinstance(
            sources,
            list,
        ):
            return ""

        result: list[str] = []

        seen: set[str] = set()

        for source in sources:
            if not isinstance(
                source,
                dict,
            ):
                continue

            source_name = str(
                source.get(
                    "source_file",
                )
                or source.get(
                    "file_name",
                )
                or source.get(
                    "source",
                )
                or source.get(
                    "source_id",
                )
                or source.get(
                    "document_id",
                )
                or source.get(
                    "technical_assignment_id",
                )
                or "Источник"
            )

            parts = [
                source_name,
            ]

            page = cls._normalize_page(
                source.get(
                    "page",
                    source.get(
                        "page_number",
                    ),
                )
            )

            if page is not None:
                parts.append(
                    f"стр. {page}",
                )

            point_id = source.get(
                "point_id",
            )

            if point_id:
                parts.append(
                    f"п. {point_id}",
                )

            score = source.get(
                "score",
            )

            if score is not None:
                parts.append(
                    f"score={score}",
                )

            value = ", ".join(
                parts,
            )

            if value in seen:
                continue

            seen.add(
                value,
            )

            result.append(
                value,
            )

        return "\n".join(
            result,
        )

    @staticmethod
    def _append_field(
        fields: list[AnalysisPdfReportField],
        label: str,
        value: object,
    ) -> None:
        """Добавляет непустое field value."""
        if value is None:
            return

        text = str(
            value,
        ).strip()

        if not text:
            return

        fields.append(
            AnalysisPdfReportField(
                label=label,
                value=text,
            )
        )

    @staticmethod
    def _annotation_content(
        fields: tuple[
            AnalysisPdfReportField,
            ...,
        ],
    ) -> str:
        """Строит popup text PDF annotation."""
        return "\n\n".join((f"{field.label}: {field.value}") for field in fields)

    @classmethod
    def _default_page(
        cls,
        result: dict[
            str,
            Any,
        ],
    ) -> int:
        """Возвращает fallback physical page number."""
        page = result.get(
            "page",
        )

        if isinstance(
            page,
            dict,
        ):
            page_number = cls._normalize_page(
                page.get(
                    "page_number",
                )
            )

            if page_number is not None:
                return page_number

        selected_pages = result.get(
            "selected_pages",
        )

        if (
            isinstance(
                selected_pages,
                list,
            )
            and selected_pages
        ):
            page_number = cls._normalize_page(selected_pages[0])

            if page_number is not None:
                return page_number

        return 1

    @staticmethod
    def _normalize_page(
        value: object,
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

    @staticmethod
    def _output_file_name(
        source_file_name: str,
    ) -> str:
        """Формирует безопасное имя downloadable PDF."""
        stem = (
            Path(
                source_file_name,
            ).stem.strip()
            or "analysis"
        )

        stem = re.sub(
            r'[\\/:*?"<>|]+',
            "_",
            stem,
        ).strip(
            " ._",
        )

        if not stem:
            stem = "analysis"

        return f"{stem}_annotated.pdf"
