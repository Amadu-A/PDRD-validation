# services/analysis-service/src/pdrd_analysis_service/application/use_cases.py

"""Use cases Analysis Service."""

import re
from dataclasses import dataclass
from typing import Any

from pdrd_analysis_service.application.json_schemas import (
    FINDING_CATEGORIES,
    FINDING_SEVERITIES,
    FINDING_STATUSES,
    build_finalization_schema,
    build_normative_check_schema,
    build_page_facts_schema,
)
from pdrd_analysis_service.application.ports.vision_model import (
    StructuredVisionModel,
    VisionModelError,
)
from pdrd_analysis_service.application.prompts import (
    build_experience_query,
    build_finalization_prompt,
    build_normative_check_prompt,
    build_page_understanding_prompt,
)
from pdrd_analysis_service.domain.analysis import (
    ExperienceSource,
    FinalFinding,
    FindingCategory,
    FindingDraft,
    FindingSeverity,
    FindingStatus,
    GenerationMetrics,
    NormativeSource,
    PageFacts,
    ReadinessReport,
)

_POSITIVE_COMPLIANCE_MARKERS = (
    "соответствует требован",
    "соответствует рекоменда",
    "соответствует норм",
    "выполнено в соответствии",
    "выполнено правильно",
    "требование выполнено",
    "требования выполнены",
    "требование соблюдено",
    "требования соблюдены",
    "нарушений не выявлено",
    "нарушение отсутствует",
    "как это сделано на листе",
    "что соответствует",
)

_NEGATIVE_VIOLATION_MARKERS = (
    "не соответствует",
    "не выполн",
    "не указан",
    "не указана",
    "не указаны",
    "не соблюд",
    "отсутств",
    "противореч",
    "недостаточ",
    "ошиб",
    "невер",
    "некоррект",
    "требуется исправ",
    "необходимо исправ",
    "необходимо добавить",
    "требуется добавить",
    "нарушено",
    "нарушены",
    "выявлено нарушение",
    "выявлены нарушения",
)


def zero_metrics(
    reason: str,
) -> GenerationMetrics:
    """Возвращает пустые метрики этапа без VLM-вызова."""
    return GenerationMetrics(
        attempt=0,
        done_reason=reason,
        requested_num_predict=0,
        total_duration_ms=0.0,
        load_duration_ms=0.0,
        prompt_eval_count=0,
        eval_count=0,
        content_length=0,
        thinking_length=0,
    )


def _string_tuple(
    value: Any,
    *,
    limit: int,
) -> tuple[str, ...]:
    """Нормализует список строк из VLM JSON."""
    if not isinstance(
        value,
        list,
    ):
        return ()

    result: list[str] = []

    for item in value:
        normalized = str(
            item,
        ).strip()

        if not normalized:
            continue

        result.append(
            normalized,
        )

        if len(result) >= limit:
            break

    return tuple(
        result,
    )


def _normalize_text(
    value: Any,
) -> str:
    """Нормализует строку для фильтрации."""
    return re.sub(
        r"\s+",
        " ",
        str(value or "").lower(),
    ).strip()


def _looks_like_compliance_confirmation(
    violation: dict[str, Any],
) -> bool:
    """Определяет ложное замечание о соответствии."""
    combined = " ".join(
        [
            _normalize_text(
                violation.get(
                    "comment",
                )
            ),
            _normalize_text(
                violation.get(
                    "evidence",
                )
            ),
            _normalize_text(
                violation.get(
                    "recommendation_draft",
                )
            ),
        ]
    )

    has_positive = any(marker in combined for marker in (_POSITIVE_COMPLIANCE_MARKERS))

    has_negative = any(marker in combined for marker in (_NEGATIVE_VIOLATION_MARKERS))

    return has_positive and not has_negative


def _filter_violations(
    violations: Any,
) -> list[dict[str, Any]]:
    """Удаляет compliance confirmations и дубли."""
    if not isinstance(
        violations,
        list,
    ):
        return []

    result: list[dict[str, Any]] = []

    seen: set[str] = set()

    for violation in violations:
        if not isinstance(
            violation,
            dict,
        ):
            continue

        if _looks_like_compliance_confirmation(
            violation,
        ):
            continue

        comment = _normalize_text(
            violation.get(
                "comment",
            )
        )

        if not comment:
            continue

        dedupe_key = re.sub(
            r"[^a-zа-яё0-9]+",
            " ",
            comment,
            flags=re.IGNORECASE,
        ).strip()

        if dedupe_key in seen:
            continue

        seen.add(
            dedupe_key,
        )

        result.append(
            violation,
        )

    return result


def _build_basis(
    sources: tuple[
        NormativeSource,
        ...,
    ],
) -> str:
    """Формирует нормативное основание."""
    parts: list[str] = []

    for source in sources:
        if not source.source_file:
            continue

        if source.page is None:
            parts.append(
                source.source_file,
            )
        else:
            parts.append(f"{source.source_file}, PDF стр. {source.page}")

    return "; ".join(
        parts,
    )


def _category(
    value: Any,
) -> FindingCategory:
    normalized = str(
        value,
    )

    if normalized in FINDING_CATEGORIES:
        return normalized  # type: ignore[return-value]

    return "other"


def _severity(
    value: Any,
) -> FindingSeverity:
    normalized = str(
        value,
    )

    if normalized in FINDING_SEVERITIES:
        return normalized  # type: ignore[return-value]

    return "warning"


def _status(
    value: Any,
) -> FindingStatus:
    normalized = str(
        value,
    )

    if normalized in FINDING_STATUSES:
        return normalized  # type: ignore[return-value]

    return "needs_review"


def _confidence(
    value: Any,
) -> float:
    try:
        result = float(
            value,
        )
    except (
        TypeError,
        ValueError,
    ):
        return 0.0

    return min(
        max(
            result,
            0.0,
        ),
        1.0,
    )


@dataclass(frozen=True, slots=True)
class UnderstandPage:
    """Получает факты листа без поиска нарушений."""

    vision_model: StructuredVisionModel

    num_predict: int

    async def execute(
        self,
        *,
        page_number: int,
        heuristic_page_type: str,
        extracted_text: str,
        image_bytes: bytes,
    ) -> tuple[
        PageFacts,
        GenerationMetrics,
    ]:
        """Выполняет structured VLM understanding."""
        result = await self.vision_model.generate_json(
            prompt=(
                build_page_understanding_prompt(
                    page_number=page_number,
                    heuristic_page_type=(heuristic_page_type),
                    extracted_text=(extracted_text),
                )
            ),
            schema=(build_page_facts_schema()),
            num_predict=self.num_predict,
            seed=100,
            stage=(f"page_understanding:{page_number}"),
            image_bytes=image_bytes,
        )

        payload = result.payload

        facts = PageFacts(
            discipline=str(
                payload.get(
                    "discipline",
                    "",
                )
            ).strip(),
            page_type=(
                str(
                    payload.get(
                        "page_type",
                        "",
                    )
                ).strip()
                or heuristic_page_type
            ),
            summary=str(
                payload.get(
                    "summary",
                    "",
                )
            ).strip(),
            objects=_string_tuple(
                payload.get(
                    "objects",
                ),
                limit=15,
            ),
            connections=_string_tuple(
                payload.get(
                    "connections",
                ),
                limit=12,
            ),
            labels=_string_tuple(
                payload.get(
                    "labels",
                ),
                limit=15,
            ),
            normative_queries=_string_tuple(
                payload.get(
                    "normative_queries",
                ),
                limit=6,
            ),
        )

        return (
            facts,
            result.metrics,
        )


@dataclass(frozen=True, slots=True)
class BuildNormativeQueries:
    """Строит retrieval queries без обращения к VLM."""

    max_queries: int = 7

    def execute(
        self,
        *,
        page_facts: PageFacts,
        extracted_text: str,
        project_context_texts: tuple[
            str,
            ...,
        ] = (),
    ) -> tuple[str, ...]:
        """Строит нейтральные запросы к Knowledge Service."""
        queries = [
            query.strip() for query in (page_facts.normative_queries) if query.strip()
        ]

        objects = "; ".join(page_facts.objects[:10])

        connections = "; ".join(page_facts.connections[:8])

        labels = "; ".join(page_facts.labels[:10])

        pz_hint = " ".join(text[:300] for text in (project_context_texts[:3]))

        queries.append(
            (
                "Подобрать применимые требования для проверки "
                "инженерного листа. "
                f"Дисциплина: {page_facts.discipline}. "
                f"Тип листа: {page_facts.page_type}. "
                f"Содержание: {page_facts.summary}. "
                f"Объекты: {objects}. "
                f"Связи: {connections}. "
                f"Обозначения: {labels}. "
                f"Контекст ПЗ проекта: {pz_hint}"
            ).strip()
        )

        if not queries and extracted_text.strip():
            queries.append(
                "Подобрать применимые нормативные требования: " + extracted_text[:1800]
            )

        result: list[str] = []
        seen: set[str] = set()

        for query in queries:
            normalized = query.strip()

            if not normalized or normalized in seen:
                continue

            seen.add(
                normalized,
            )

            result.append(
                normalized,
            )

        return tuple(result[: self.max_queries])


@dataclass(frozen=True, slots=True)
class CheckPageAgainstNorms:
    """Проверяет лист только по переданным нормам."""

    vision_model: StructuredVisionModel

    num_predict: int
    max_issues: int
    normative_text_limit: int

    async def execute(
        self,
        *,
        page_number: int,
        extracted_text: str,
        page_facts: PageFacts,
        normative_sources: tuple[
            NormativeSource,
            ...,
        ],
        image_bytes: bytes,
    ) -> tuple[
        str,
        tuple[
            FindingDraft,
            ...,
        ],
        GenerationMetrics,
    ]:
        """Выполняет нормативную VLM-проверку."""
        source_ids = tuple(
            source.source_id for source in normative_sources if source.source_id
        )

        if not source_ids:
            return (
                ("Нормативные источники для листа не найдены."),
                (),
                zero_metrics(
                    "no_normative_sources",
                ),
            )

        result = await self.vision_model.generate_json(
            prompt=(
                build_normative_check_prompt(
                    page_number=page_number,
                    extracted_text=(extracted_text),
                    page_facts=page_facts,
                    normative_sources=(normative_sources),
                    normative_text_limit=(self.normative_text_limit),
                )
            ),
            schema=(
                build_normative_check_schema(
                    source_ids=source_ids,
                    max_issues=(self.max_issues),
                )
            ),
            num_predict=self.num_predict,
            seed=200,
            stage=(f"normative_check:{page_number}"),
            image_bytes=image_bytes,
        )

        source_by_id = {source.source_id: source for source in normative_sources}

        findings: list[FindingDraft] = []

        for violation in _filter_violations(
            result.payload.get(
                "violations",
                [],
            )
        ):
            requested_ids = _string_tuple(
                violation.get(
                    "normative_source_ids",
                ),
                limit=3,
            )

            selected_sources = tuple(
                source_by_id[source_id]
                for source_id in requested_ids
                if source_id in source_by_id
            )

            if not selected_sources:
                continue

            comment = str(
                violation.get(
                    "comment",
                    "",
                )
            ).strip()

            evidence = str(
                violation.get(
                    "evidence",
                    "",
                )
            ).strip()

            recommendation_draft = str(
                violation.get(
                    "recommendation_draft",
                    "",
                )
            ).strip()

            finding_id = f"p{page_number}-f{len(findings) + 1}"

            category = _category(
                violation.get(
                    "category",
                )
            )

            findings.append(
                FindingDraft(
                    finding_id=finding_id,
                    page=page_number,
                    page_type=(page_facts.page_type),
                    category=category,
                    severity=_severity(
                        violation.get(
                            "severity",
                        )
                    ),
                    status=_status(
                        violation.get(
                            "status",
                        )
                    ),
                    comment=comment,
                    evidence=evidence,
                    recommendation_draft=(recommendation_draft),
                    confidence=_confidence(
                        violation.get(
                            "confidence",
                        )
                    ),
                    normative_source_ids=tuple(
                        source.source_id for source in (selected_sources)
                    ),
                    basis=_build_basis(
                        selected_sources,
                    ),
                    basis_sources=(selected_sources),
                    experience_query=(
                        build_experience_query(
                            category=category,
                            comment=comment,
                            evidence=evidence,
                            recommendation_draft=(recommendation_draft),
                        )
                    ),
                )
            )

        return (
            str(
                result.payload.get(
                    "summary",
                    "",
                )
            ).strip(),
            tuple(
                findings,
            ),
            result.metrics,
        )


@dataclass(frozen=True, slots=True)
class FinalizeFindings:
    """Финализирует нормативные findings небольшими batches."""

    vision_model: StructuredVisionModel

    num_predict: int
    batch_size: int

    experience_context_limit: int
    experience_min_score: float

    async def execute(
        self,
        *,
        findings: tuple[
            FindingDraft,
            ...,
        ],
        experience_by_finding: dict[
            str,
            tuple[
                ExperienceSource,
                ...,
            ],
        ],
    ) -> tuple[
        str,
        tuple[
            FinalFinding,
            ...,
        ],
        dict[str, Any],
    ]:
        """Оформляет findings и использует только релевантный опыт."""
        if not findings:
            return (
                "",
                (),
                {
                    "attempt": 0,
                    "done_reason": ("no_findings"),
                    "batch_size": (self.batch_size),
                    "fallback_count": 0,
                    "batches": [],
                },
            )

        eligible_experience = {
            finding_id: tuple(
                source
                for source in sources
                if (source.score >= self.experience_min_score)
            )
            for finding_id, sources in (experience_by_finding.items())
        }

        final_items: list[FinalFinding] = []

        batch_metrics: list[dict[str, Any]] = []

        fallback_count = 0

        for start in range(
            0,
            len(
                findings,
            ),
            self.batch_size,
        ):
            batch = findings[start : start + self.batch_size]

            finding_ids = tuple(finding.finding_id for finding in batch)

            try:
                generation = await self.vision_model.generate_json(
                    prompt=(
                        build_finalization_prompt(
                            findings=batch,
                            experience_by_finding=(eligible_experience),
                            experience_context_limit=(self.experience_context_limit),
                        )
                    ),
                    schema=(
                        build_finalization_schema(
                            finding_ids,
                        )
                    ),
                    num_predict=(self.num_predict),
                    seed=(300 + start),
                    stage=(
                        "finalization:"
                        + ",".join(
                            finding_ids,
                        )
                    ),
                    image_bytes=None,
                )

                returned = {
                    str(
                        item.get(
                            "finding_id",
                            "",
                        )
                    ): item
                    for item in (
                        generation.payload.get(
                            "findings",
                            [],
                        )
                    )
                    if isinstance(
                        item,
                        dict,
                    )
                }

                for finding in batch:
                    item = returned.get(
                        finding.finding_id,
                    )

                    if item is None:
                        final_items.append(
                            self._fallback(
                                finding,
                            )
                        )

                        fallback_count += 1
                        continue

                    final_items.append(
                        self._build_final(
                            finding=finding,
                            item=item,
                            available_experience=(
                                eligible_experience.get(
                                    finding.finding_id,
                                    (),
                                )
                            ),
                        )
                    )

                batch_metrics.append(
                    {
                        "finding_ids": list(
                            finding_ids,
                        ),
                        "fallback": False,
                        **generation.metrics.as_dict(),
                    }
                )

            except VisionModelError as error:
                fallback_count += len(
                    batch,
                )

                final_items.extend(
                    self._fallback(
                        finding,
                    )
                    for finding in batch
                )

                batch_metrics.append(
                    {
                        "finding_ids": list(
                            finding_ids,
                        ),
                        "fallback": True,
                        "error": str(
                            error,
                        )[:1200],
                    }
                )

        return (
            ("Замечания сформированы по результатам нормативной проверки."),
            tuple(
                final_items,
            ),
            {
                "attempt": 1,
                "done_reason": (
                    "completed_with_fallback" if fallback_count else "stop"
                ),
                "batch_size": (self.batch_size),
                "fallback_count": (fallback_count),
                "experience_min_score": (self.experience_min_score),
                "batches": batch_metrics,
            },
        )

    @staticmethod
    def _fallback(
        finding: FindingDraft,
    ) -> FinalFinding:
        """Возвращает finding без stylistic VLM."""
        recommendation = finding.recommendation_draft.strip()

        if not recommendation:
            recommendation = (
                "Проверить указанное несоответствие "
                "и скорректировать проектное решение "
                "по приведённому нормативному основанию."
            )

        return FinalFinding(
            finding_id=finding.finding_id,
            page=finding.page,
            page_type=finding.page_type,
            category=finding.category,
            severity=finding.severity,
            status=finding.status,
            comment=finding.comment,
            evidence=finding.evidence,
            recommendation=recommendation,
            confidence=finding.confidence,
            basis=finding.basis,
            basis_sources=(finding.basis_sources),
            experience_sources=(),
        )

    @staticmethod
    def _build_final(
        *,
        finding: FindingDraft,
        item: dict[str, Any],
        available_experience: tuple[
            ExperienceSource,
            ...,
        ],
    ) -> FinalFinding:
        """Собирает итоговый finding и проверяет E-id."""
        experience_by_id = {
            source.source_id: source for source in (available_experience)
        }

        requested_ids = _string_tuple(
            item.get(
                "experience_source_ids",
            ),
            limit=2,
        )

        selected_experience = tuple(
            experience_by_id[source_id]
            for source_id in requested_ids
            if source_id in experience_by_id
        )

        comment = str(
            item.get(
                "comment",
                "",
            )
        ).strip()

        recommendation = str(
            item.get(
                "recommendation",
                "",
            )
        ).strip()

        if not comment:
            comment = finding.comment

        if not recommendation:
            recommendation = finding.recommendation_draft

        return FinalFinding(
            finding_id=finding.finding_id,
            page=finding.page,
            page_type=finding.page_type,
            category=finding.category,
            severity=finding.severity,
            status=finding.status,
            comment=comment,
            evidence=finding.evidence,
            recommendation=(recommendation),
            confidence=finding.confidence,
            basis=finding.basis,
            basis_sources=(finding.basis_sources),
            experience_sources=(selected_experience),
        )


@dataclass(frozen=True, slots=True)
class CheckReadiness:
    """Проверяет readiness VLM provider."""

    vision_model: StructuredVisionModel

    async def execute(
        self,
    ) -> ReadinessReport:
        """Возвращает readiness report."""
        return ReadinessReport(vision_model=(await self.vision_model.is_ready()))
