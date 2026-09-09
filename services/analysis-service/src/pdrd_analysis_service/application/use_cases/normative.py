# services/analysis-service/src/pdrd_analysis_service/application/use_cases/normative.py

"""Use cases retrieval preparation и инженерной проверки листа."""

import logging
from dataclasses import dataclass

from pdrd_analysis_service.application.json_schemas import (
    build_normative_check_schema,
)
from pdrd_analysis_service.application.ports.vision_model import (
    StructuredVisionModel,
)
from pdrd_analysis_service.application.prompts import (
    build_experience_query,
    build_normative_check_prompt,
)
from pdrd_analysis_service.application.use_cases.common import (
    build_basis,
    category,
    confidence,
    finding_status,
    select_violation_candidates,
    severity,
    string_tuple,
)
from pdrd_analysis_service.domain.analysis import (
    FindingDraft,
    GenerationMetrics,
    NormativeSource,
    PageFacts,
    TechnicalAssignmentConflictCandidate,
    TechnicalAssignmentSource,
    UserPackageSource,
)

logger = logging.getLogger(
    __name__,
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
    ) -> tuple[
        str,
        ...,
    ]:
        """Строит нейтральные запросы к Knowledge Service."""
        queries = [
            query.strip() for query in page_facts.normative_queries if query.strip()
        ]

        objects = "; ".join(
            page_facts.objects[:10],
        )

        connections = "; ".join(
            page_facts.connections[:8],
        )

        labels = "; ".join(
            page_facts.labels[:10],
        )

        pz_hint = " ".join(text[:300] for text in project_context_texts[:3])

        queries.append(
            (
                "Подобрать применимые требования "
                "для проверки инженерного листа. "
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

        return tuple(
            result[: self.max_queries],
        )


@dataclass(frozen=True, slots=True)
class CheckPageAgainstNorms:
    """Выполняет инженерную и N/T/U проверку одного листа."""

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
        normative_system_prompt: str | None = None,
        technical_assignment_sources: tuple[
            TechnicalAssignmentSource,
            ...,
        ] = (),
        conflict_candidates: tuple[
            TechnicalAssignmentConflictCandidate,
            ...,
        ] = (),
        user_package_sources: tuple[
            UserPackageSource,
            ...,
        ] = (),
    ) -> tuple[
        str,
        tuple[
            FindingDraft,
            ...,
        ],
        GenerationMetrics,
    ]:
        """Проверяет сам лист и сопоставляет его с N/T/U evidence."""
        normative_source_ids = tuple(
            source.source_id for source in normative_sources if source.source_id
        )

        technical_assignment_source_ids = tuple(
            source.source_id
            for source in technical_assignment_sources
            if source.source_id
        )

        user_package_source_ids = tuple(
            source.source_id for source in user_package_sources if source.source_id
        )

        result = await self.vision_model.generate_json(
            prompt=build_normative_check_prompt(
                page_number=page_number,
                extracted_text=extracted_text,
                page_facts=page_facts,
                normative_sources=normative_sources,
                technical_assignment_sources=technical_assignment_sources,
                conflict_candidates=conflict_candidates,
                user_package_sources=user_package_sources,
                normative_text_limit=self.normative_text_limit,
                normative_system_prompt=normative_system_prompt,
            ),
            schema=build_normative_check_schema(
                source_ids=normative_source_ids,
                technical_assignment_source_ids=(technical_assignment_source_ids),
                user_package_source_ids=user_package_source_ids,
                max_issues=self.max_issues,
            ),
            num_predict=self.num_predict,
            seed=200,
            stage=f"normative_check:{page_number}",
            image_bytes=image_bytes,
        )

        source_by_id = {source.source_id: source for source in normative_sources}

        technical_assignment_by_id = {
            source.source_id: source for source in technical_assignment_sources
        }

        user_package_by_id = {
            source.source_id: source for source in user_package_sources
        }

        candidate_selection = select_violation_candidates(
            result.payload.get(
                "violations",
                [],
            )
        )

        logger.info(
            (
                "normative_candidate_selection "
                "page=%s generated=%s preserved=%s "
                "rejected=%s rejection_reasons=%s"
            ),
            page_number,
            candidate_selection.generated_count,
            candidate_selection.preserved_count,
            candidate_selection.rejected_count,
            candidate_selection.rejection_counts,
        )

        findings: list[FindingDraft] = []

        for violation in candidate_selection.candidates:
            requested_normative_ids = string_tuple(
                violation.get(
                    "normative_source_ids",
                ),
                limit=3,
            )

            requested_technical_assignment_ids = string_tuple(
                violation.get(
                    "technical_assignment_source_ids",
                ),
                limit=3,
            )

            requested_user_package_ids = string_tuple(
                violation.get(
                    "user_package_source_ids",
                ),
                limit=3,
            )

            selected_normative_sources = tuple(
                source_by_id[source_id]
                for source_id in requested_normative_ids
                if source_id in source_by_id
            )

            selected_technical_assignment_sources = tuple(
                technical_assignment_by_id[source_id]
                for source_id in requested_technical_assignment_ids
                if source_id in technical_assignment_by_id
            )

            selected_user_package_sources = tuple(
                user_package_by_id[source_id]
                for source_id in requested_user_package_ids
                if source_id in user_package_by_id
            )

            detached_normative_ids = tuple(
                source_id
                for source_id in requested_normative_ids
                if source_id not in source_by_id
            )

            detached_technical_assignment_ids = tuple(
                source_id
                for source_id in requested_technical_assignment_ids
                if source_id not in technical_assignment_by_id
            )

            detached_user_package_ids = tuple(
                source_id
                for source_id in requested_user_package_ids
                if source_id not in user_package_by_id
            )

            if (
                detached_normative_ids
                or detached_technical_assignment_ids
                or detached_user_package_ids
            ):
                logger.info(
                    (
                        "normative_candidate_source_ids_detached "
                        "page=%s candidate=%s "
                        "normative=%s technical_assignment=%s "
                        "user_package=%s"
                    ),
                    page_number,
                    len(
                        findings,
                    )
                    + 1,
                    detached_normative_ids,
                    detached_technical_assignment_ids,
                    detached_user_package_ids,
                )

            selected_any_source = bool(
                selected_normative_sources
                or selected_technical_assignment_sources
                or selected_user_package_sources
            )

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

            finding_category = category(
                violation.get(
                    "category",
                )
            )

            if (
                not selected_normative_sources
                and (
                    selected_technical_assignment_sources
                    or selected_user_package_sources
                )
                and finding_category == "normative_control"
            ):
                finding_category = "customer_requirements"

            if not selected_any_source and finding_category in {
                "normative_control",
                "customer_requirements",
            }:
                finding_category = "other"

            normalized_status = finding_status(
                violation.get(
                    "status",
                )
            )

            if not selected_any_source:
                normalized_status = "needs_review"

            findings.append(
                FindingDraft(
                    finding_id=finding_id,
                    page=page_number,
                    page_type=page_facts.page_type,
                    category=finding_category,
                    severity=severity(
                        violation.get(
                            "severity",
                        )
                    ),
                    status=normalized_status,
                    comment=comment,
                    evidence=evidence,
                    recommendation_draft=(recommendation_draft),
                    confidence=confidence(
                        violation.get(
                            "confidence",
                        )
                    ),
                    normative_source_ids=tuple(
                        source.source_id for source in selected_normative_sources
                    ),
                    basis=build_basis(
                        selected_normative_sources,
                    ),
                    basis_sources=(selected_normative_sources),
                    experience_query=build_experience_query(
                        category=finding_category,
                        comment=comment,
                        evidence=evidence,
                        recommendation_draft=(recommendation_draft),
                    ),
                    technical_assignment_source_ids=tuple(
                        source.source_id
                        for source in selected_technical_assignment_sources
                    ),
                    technical_assignment_basis_sources=(
                        selected_technical_assignment_sources
                    ),
                    user_package_source_ids=tuple(
                        source.source_id for source in selected_user_package_sources
                    ),
                    user_package_basis_sources=(selected_user_package_sources),
                )
            )

        logger.info(
            ("normative_findings_preserved page=%s candidates=%s findings=%s"),
            page_number,
            candidate_selection.preserved_count,
            len(
                findings,
            ),
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
