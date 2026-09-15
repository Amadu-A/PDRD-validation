# services/analysis-service/src/pdrd_analysis_service/application/use_cases/project_context.py

"""Application use cases Project Context / Пояснительной записки."""

import logging
import re
from dataclasses import dataclass

from pdrd_analysis_service.application.ports.vision_model import (
    StructuredVisionModel,
)
from pdrd_analysis_service.application.project_context_prompt import (
    build_project_context_classification_prompt,
)
from pdrd_analysis_service.application.project_context_schema import (
    build_project_context_classification_schema,
)
from pdrd_analysis_service.domain.analysis import (
    GenerationMetrics,
    PageFacts,
)
from pdrd_analysis_service.domain.project_context import (
    InvalidProjectContextError,
    ProjectContextAugmentation,
    ProjectContextClassification,
    ProjectContextPage,
    ProjectContextPageKind,
    ProjectContextSource,
    ProjectContextValidation,
)

logger = logging.getLogger(
    __name__,
)


def _normalize_context_text(
    text: str,
) -> str:
    """Нормализует текст ПЗ без потери paragraph structure."""
    result = text.replace(
        "\x00",
        " ",
    )

    result = re.sub(
        r"[ \t]+",
        " ",
        result,
    )

    result = re.sub(
        r"\n{3,}",
        "\n\n",
        result,
    )

    return result.strip()


@dataclass(frozen=True, slots=True)
class ValidateProjectContext:
    """Проверяет, что выбранный диапазон действительно похож на ПЗ."""

    vision_model: StructuredVisionModel

    classify_batch_size: int
    classify_num_predict: int

    min_text_length: int
    reject_confidence: float

    async def execute(
        self,
        *,
        enabled: bool,
        pages: tuple[
            ProjectContextPage,
            ...,
        ],
        cached_validation: (ProjectContextValidation | None) = None,
    ) -> tuple[
        ProjectContextValidation,
        tuple[
            GenerationMetrics,
            ...,
        ],
    ]:
        """Классифицирует ПЗ либо безопасно переиспользует cache."""
        if not enabled:
            return (
                ProjectContextValidation(
                    enabled=False,
                    pages_count=0,
                    classifications=(),
                    warnings=(),
                ),
                (),
            )

        normalized_pages = self._normalize_and_validate_pages(
            pages,
        )

        if cached_validation is not None:
            validation = self._reuse_cached_validation(
                pages=normalized_pages,
                cached_validation=(cached_validation),
            )

            logger.info(
                ("project_context_validation_cache hit=true pages=%s"),
                validation.pages_count,
            )

            return (
                validation,
                (),
            )

        classifications: list[ProjectContextClassification] = []

        metrics: list[GenerationMetrics] = []

        for start in range(
            0,
            len(
                normalized_pages,
            ),
            self.classify_batch_size,
        ):
            batch = normalized_pages[start : start + self.classify_batch_size]

            page_numbers = tuple(page.page_number for page in batch)

            generation = await self.vision_model.generate_json(
                prompt=(
                    build_project_context_classification_prompt(
                        batch,
                    )
                ),
                schema=(
                    build_project_context_classification_schema(
                        page_numbers,
                    )
                ),
                num_predict=(self.classify_num_predict),
                seed=(400 + start),
                stage=(
                    "project_context_validation:"
                    + ",".join(
                        str(
                            page_number,
                        )
                        for page_number in page_numbers
                    )
                ),
                image_bytes=None,
            )

            metrics.append(
                generation.metrics,
            )

            items = generation.payload.get(
                "pages",
                [],
            )

            if not isinstance(
                items,
                list,
            ):
                raise InvalidProjectContextError(
                    "Модель вернула некорректный формат проверки ПЗ.",
                )

            for item in items:
                if not isinstance(
                    item,
                    dict,
                ):
                    continue

                classification = self._classification_from_model(
                    item,
                )

                if classification is not None:
                    classifications.append(
                        classification,
                    )

        validation = self._build_validation(
            pages=normalized_pages,
            classifications=tuple(
                classifications,
            ),
        )

        logger.info(
            ("project_context_validation_cache hit=false pages=%s vlm_calls=%s"),
            validation.pages_count,
            len(
                metrics,
            ),
        )

        return (
            validation,
            tuple(
                metrics,
            ),
        )

    def _normalize_and_validate_pages(
        self,
        pages: tuple[
            ProjectContextPage,
            ...,
        ],
    ) -> tuple[
        ProjectContextPage,
        ...,
    ]:
        """Нормализует диапазон и выполняет дешёвые deterministic checks."""
        if not pages:
            raise InvalidProjectContextError(
                "Диапазон ПЗ не содержит страниц.",
            )

        normalized_pages = tuple(
            ProjectContextPage(
                page_number=(page.page_number),
                text=(
                    _normalize_context_text(
                        page.text,
                    )
                ),
            )
            for page in pages
        )

        page_numbers = tuple(page.page_number for page in normalized_pages)

        if len(
            set(
                page_numbers,
            )
        ) != len(
            page_numbers,
        ):
            raise InvalidProjectContextError(
                "Диапазон ПЗ содержит повторяющиеся номера страниц.",
            )

        too_short = tuple(
            page.page_number
            for page in normalized_pages
            if (
                len(
                    page.text,
                )
                < self.min_text_length
            )
        )

        if too_short:
            page_text = ", ".join(
                str(
                    page_number,
                )
                for page_number in too_short
            )

            raise InvalidProjectContextError(
                "На страницах ПЗ недостаточно "
                "извлекаемого текста: "
                f"{page_text}. "
                "Проверьте диапазон. "
                "Для сканированной ПЗ потребуется OCR.",
            )

        return normalized_pages

    def _reuse_cached_validation(
        self,
        *,
        pages: tuple[
            ProjectContextPage,
            ...,
        ],
        cached_validation: ProjectContextValidation,
    ) -> ProjectContextValidation:
        """Проверяет cached snapshot без повторного обращения к VLM."""
        if not cached_validation.enabled:
            raise InvalidProjectContextError(
                "Reusable PZ cache содержит disabled validation.",
            )

        if cached_validation.pages_count != len(
            pages,
        ):
            raise InvalidProjectContextError(
                "Reusable PZ cache содержит validation для другого количества страниц.",
            )

        expected_pages = tuple(page.page_number for page in pages)

        actual_pages = tuple(
            item.page_number for item in cached_validation.classifications
        )

        if actual_pages != expected_pages:
            raise InvalidProjectContextError(
                "Reusable PZ cache содержит validation для другого диапазона страниц.",
            )

        return self._build_validation(
            pages=pages,
            classifications=(cached_validation.classifications),
        )

    def _build_validation(
        self,
        *,
        pages: tuple[
            ProjectContextPage,
            ...,
        ],
        classifications: tuple[
            ProjectContextClassification,
            ...,
        ],
    ) -> ProjectContextValidation:
        """Проверяет полноту classification и применяет reject policy."""
        by_page: dict[
            int,
            ProjectContextClassification,
        ] = {}

        duplicate_pages: set[int] = set()

        for item in classifications:
            if item.page_number in by_page:
                duplicate_pages.add(
                    item.page_number,
                )

            by_page[item.page_number] = item

        if duplicate_pages:
            duplicate_text = ", ".join(
                str(
                    page_number,
                )
                for page_number in sorted(
                    duplicate_pages,
                )
            )

            raise InvalidProjectContextError(
                f"Модель вернула повторную классификацию страниц ПЗ: {duplicate_text}.",
            )

        expected_pages = tuple(page.page_number for page in pages)

        missing_pages = tuple(
            page_number for page_number in expected_pages if page_number not in by_page
        )

        if missing_pages:
            missing_text = ", ".join(
                str(
                    page_number,
                )
                for page_number in missing_pages
            )

            raise InvalidProjectContextError(
                f"Модель не вернула классификацию страниц ПЗ: {missing_text}.",
            )

        unexpected_pages = tuple(
            page_number for page_number in by_page if page_number not in expected_pages
        )

        if unexpected_pages:
            unexpected_text = ", ".join(
                str(
                    page_number,
                )
                for page_number in sorted(
                    unexpected_pages,
                )
            )

            raise InvalidProjectContextError(
                f"Validation содержит лишние страницы ПЗ: {unexpected_text}.",
            )

        ordered = tuple(by_page[page_number] for page_number in expected_pages)

        rejected = tuple(
            item
            for item in ordered
            if (
                item.kind is not ProjectContextPageKind.EXPLANATORY_NOTE
                and item.confidence >= self.reject_confidence
            )
        )

        if rejected:
            details = "; ".join(
                (f"стр. {item.page_number}: {item.kind.value} ({item.reason})")
                for item in rejected
            )

            raise InvalidProjectContextError(
                "Выбранный диапазон похож "
                "не только на пояснительную записку. "
                f"Проверьте страницы: {details}",
            )

        warnings = tuple(
            item
            for item in ordered
            if (
                item.kind is not ProjectContextPageKind.EXPLANATORY_NOTE
                and item.confidence < self.reject_confidence
            )
        )

        return ProjectContextValidation(
            enabled=True,
            pages_count=len(
                pages,
            ),
            classifications=ordered,
            warnings=warnings,
        )

    @staticmethod
    def _classification_from_model(
        item: dict[
            object,
            object,
        ],
    ) -> ProjectContextClassification | None:
        """Преобразует одну VLM classification в Domain."""
        try:
            page_number = int(
                item.get(
                    "page",
                )
            )

        except (
            TypeError,
            ValueError,
        ):
            return None

        kind_text = str(
            item.get(
                "kind",
                "other",
            )
        )

        try:
            kind = ProjectContextPageKind(
                kind_text,
            )

        except ValueError:
            kind = ProjectContextPageKind.OTHER

        try:
            confidence = float(
                item.get(
                    "confidence",
                    0.0,
                )
            )

        except (
            TypeError,
            ValueError,
        ):
            confidence = 0.0

        confidence = min(
            max(
                confidence,
                0.0,
            ),
            1.0,
        )

        return ProjectContextClassification(
            page_number=page_number,
            kind=kind,
            confidence=confidence,
            reason=str(
                item.get(
                    "reason",
                    "",
                )
            ).strip(),
        )


@dataclass(frozen=True, slots=True)
class BuildProjectContextQuery:
    """Строит semantic query ПЗ по фактам текущего листа."""

    source_text_limit: int = 1500

    def execute(
        self,
        *,
        page_facts: PageFacts,
        extracted_text: str,
    ) -> str:
        """Формирует query без вызова модели."""
        objects = "; ".join(page_facts.objects[:10])

        connections = "; ".join(page_facts.connections[:8])

        labels = "; ".join(page_facts.labels[:10])

        page_text = _normalize_context_text(
            extracted_text,
        )[: self.source_text_limit]

        return (
            "Найти в пояснительной записке "
            "текущего проекта сведения, которые "
            "относятся к этому листу. "
            f"Дисциплина: {page_facts.discipline}. "
            f"Тип листа: {page_facts.page_type}. "
            f"Содержание: {page_facts.summary}. "
            f"Объекты: {objects}. "
            f"Связи: {connections}. "
            f"Обозначения: {labels}. "
            f"Текст листа: {page_text}"
        )


@dataclass(frozen=True, slots=True)
class AugmentProjectContext:
    """Добавляет найденный PZ context к тексту нормативной проверки."""

    context_text_limit: int

    def execute(
        self,
        *,
        extracted_text: str,
        sources: tuple[
            ProjectContextSource,
            ...,
        ],
    ) -> ProjectContextAugmentation:
        """Формирует analysis text и PZ hints."""
        if not sources:
            return ProjectContextAugmentation(
                analysis_text=(extracted_text),
                project_context_texts=(),
                sources=(),
            )

        parts = tuple(
            (
                "["
                f"{source.source_id} | "
                f"PDF стр. {source.page} | "
                f"similarity={source.score}"
                "]\n"
                f"{source.text[: self.context_text_limit]}"
            )
            for source in sources
        )

        analysis_text = (
            "=== ТЕКСТ АНАЛИЗИРУЕМОЙ СТРАНИЦЫ ===\n"
            f"{extracted_text}\n\n"
            "=== РЕЛЕВАНТНЫЙ КОНТЕКСТ "
            "ПОЯСНИТЕЛЬНОЙ ЗАПИСКИ ===\n"
            + "\n\n".join(
                parts,
            )
            + "\n\n"
            "Контекст ПЗ является контекстом "
            "текущего проекта, а не нормативом. "
            "Используй его для понимания проектных "
            "решений и применимости норм. "
            "Нормативное нарушение по-прежнему "
            "должно подтверждаться реальным "
            "нормативным источником N-id."
        )

        return ProjectContextAugmentation(
            analysis_text=analysis_text,
            project_context_texts=tuple(source.text for source in sources),
            sources=sources,
        )
