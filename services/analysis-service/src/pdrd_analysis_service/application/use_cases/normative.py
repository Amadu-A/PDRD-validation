# services/analysis-service/src/pdrd_analysis_service/application/use_cases/normative.py

"""Use cases normative retrieval preparation и compliance check."""

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
    filter_violations,
    finding_status,
    severity,
    string_tuple,
    zero_metrics,
)
from pdrd_analysis_service.domain.analysis import (
    FindingDraft,
    GenerationMetrics,
    NormativeSource,
    PageFacts,
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
            query.strip() for query in page_facts.normative_queries if query.strip()
        ]

        objects = "; ".join(page_facts.objects[:10])

        connections = "; ".join(page_facts.connections[:8])

        labels = "; ".join(page_facts.labels[:10])

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
                    page_number=(page_number),
                    extracted_text=(extracted_text),
                    page_facts=(page_facts),
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

        for violation in filter_violations(
            result.payload.get(
                "violations",
                [],
            )
        ):
            requested_ids = string_tuple(
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

            finding_category = category(
                violation.get(
                    "category",
                )
            )

            findings.append(
                FindingDraft(
                    finding_id=(finding_id),
                    page=page_number,
                    page_type=(page_facts.page_type),
                    category=(finding_category),
                    severity=severity(
                        violation.get(
                            "severity",
                        )
                    ),
                    status=finding_status(
                        violation.get(
                            "status",
                        )
                    ),
                    comment=comment,
                    evidence=evidence,
                    recommendation_draft=(recommendation_draft),
                    confidence=confidence(
                        violation.get(
                            "confidence",
                        )
                    ),
                    normative_source_ids=(
                        tuple(source.source_id for source in selected_sources)
                    ),
                    basis=build_basis(
                        selected_sources,
                    ),
                    basis_sources=(selected_sources),
                    experience_query=(
                        build_experience_query(
                            category=(finding_category),
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
