# services/analysis-service/src/pdrd_analysis_service/application/use_cases/finalization.py

"""Use case финализации findings."""

from dataclasses import dataclass
from typing import Any

from pdrd_analysis_service.application.json_schemas import (
    build_finalization_schema,
)
from pdrd_analysis_service.application.ports.vision_model import (
    StructuredVisionModel,
    VisionModelError,
)
from pdrd_analysis_service.application.prompts import (
    build_finalization_prompt,
)
from pdrd_analysis_service.application.use_cases.common import (
    build_basis,
    string_tuple,
)
from pdrd_analysis_service.domain.analysis import (
    ExperienceSource,
    FinalFinding,
    FindingDraft,
    NormativeSource,
)


def _dedupe_normative_sources(
    sources: tuple[
        NormativeSource,
        ...,
    ],
) -> tuple[
    NormativeSource,
    ...,
]:
    """Дедуплицирует N-sources, сохраняя исходный порядок."""
    result: list[NormativeSource] = []

    seen: set[
        tuple[
            str,
            ...,
        ]
    ] = set()

    for source in sources:
        if source.point_id:
            key = (
                "point_id",
                source.point_id,
            )
        else:
            key = (
                "fallback",
                source.source_id,
                str(
                    source.document_id or "",
                ),
                str(
                    source.page or "",
                ),
                str(
                    source.chunk_index or "",
                ),
                source.text[:120],
            )

        if key in seen:
            continue

        seen.add(
            key,
        )

        result.append(
            source,
        )

    return tuple(
        result,
    )


def _available_normative_sources(
    *,
    finding: FindingDraft,
    normative_candidates: tuple[
        NormativeSource,
        ...,
    ],
) -> dict[
    str,
    NormativeSource,
]:
    """Строит разрешённый набор existing + enrichment N-sources."""
    result: dict[
        str,
        NormativeSource,
    ] = {}

    for source in (
        *finding.basis_sources,
        *normative_candidates,
    ):
        if not source.source_id:
            continue

        result.setdefault(
            source.source_id,
            source,
        )

    return result


def _fallback_recommendation(
    finding: FindingDraft,
) -> str:
    """Возвращает безопасную исходную рекомендацию."""
    recommendation = finding.recommendation_draft.strip()

    if recommendation:
        return recommendation

    return (
        "Проверить указанное несоответствие "
        "и при необходимости скорректировать "
        "проектное решение."
    )


@dataclass(frozen=True, slots=True)
class FinalizeFindings:
    """Финализирует findings и non-destructive N enrichment."""

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
        normative_candidates: tuple[
            NormativeSource,
            ...,
        ] = (),
    ) -> tuple[
        str,
        tuple[
            FinalFinding,
            ...,
        ],
        dict[str, Any],
    ]:
        """Оформляет findings, не позволяя enrichment их удалить."""
        if not findings:
            return (
                "",
                (),
                {
                    "attempt": 0,
                    "done_reason": "no_findings",
                    "batch_size": self.batch_size,
                    "fallback_count": 0,
                    "batches": [],
                },
            )

        eligible_experience = {
            finding_id: tuple(
                source
                for source in sources
                if source.score >= self.experience_min_score
            )
            for finding_id, sources in experience_by_finding.items()
        }

        normalized_candidates = _dedupe_normative_sources(
            normative_candidates,
        )

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

            allowed_normative_ids: list[str] = []

            seen_normative_ids: set[str] = set()

            for finding in batch:
                for source in (
                    *finding.basis_sources,
                    *normalized_candidates,
                ):
                    source_id = source.source_id.strip()

                    if not source_id or source_id in seen_normative_ids:
                        continue

                    seen_normative_ids.add(
                        source_id,
                    )

                    allowed_normative_ids.append(
                        source_id,
                    )

            try:
                generation = await self.vision_model.generate_json(
                    prompt=build_finalization_prompt(
                        findings=batch,
                        experience_by_finding=eligible_experience,
                        experience_context_limit=(self.experience_context_limit),
                        normative_candidates=normalized_candidates,
                    ),
                    schema=build_finalization_schema(
                        finding_ids,
                        normative_source_ids=tuple(
                            allowed_normative_ids,
                        ),
                    ),
                    num_predict=self.num_predict,
                    seed=300 + start,
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
                    for item in generation.payload.get(
                        "findings",
                        [],
                    )
                    if isinstance(
                        item,
                        dict,
                    )
                }

                batch_fallback_count = 0

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
                        batch_fallback_count += 1

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
                            normative_candidates=(normalized_candidates),
                        )
                    )

                batch_metrics.append(
                    {
                        "finding_ids": list(
                            finding_ids,
                        ),
                        "fallback": (batch_fallback_count > 0),
                        "fallback_count": (batch_fallback_count),
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
                        "fallback_count": len(
                            batch,
                        ),
                        "error": str(
                            error,
                        )[:1200],
                    }
                )

        return (
            "Замечания сформированы по результатам инженерной проверки.",
            tuple(
                final_items,
            ),
            {
                "attempt": 1,
                "done_reason": (
                    "completed_with_fallback" if fallback_count else "stop"
                ),
                "batch_size": self.batch_size,
                "fallback_count": fallback_count,
                "experience_min_score": (self.experience_min_score),
                "normative_candidates_count": len(
                    normalized_candidates,
                ),
                "batches": batch_metrics,
            },
        )

    @staticmethod
    def _fallback(
        finding: FindingDraft,
    ) -> FinalFinding:
        """Возвращает исходный finding без потери evidence."""
        return FinalFinding(
            finding_id=finding.finding_id,
            page=finding.page,
            page_type=finding.page_type,
            category=finding.category,
            severity=finding.severity,
            status=finding.status,
            comment=finding.comment,
            evidence=finding.evidence,
            recommendation=(
                _fallback_recommendation(
                    finding,
                )
            ),
            confidence=finding.confidence,
            basis=finding.basis,
            basis_sources=finding.basis_sources,
            experience_sources=(),
            technical_assignment_basis_sources=(
                finding.technical_assignment_basis_sources
            ),
            user_package_basis_sources=(finding.user_package_basis_sources),
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
        normative_candidates: tuple[
            NormativeSource,
            ...,
        ],
    ) -> FinalFinding:
        """Собирает final finding и валидирует выбранные N/E IDs."""
        experience_by_id = {source.source_id: source for source in available_experience}

        requested_experience_ids = string_tuple(
            item.get(
                "experience_source_ids",
            ),
            limit=2,
        )

        selected_experience = tuple(
            experience_by_id[source_id]
            for source_id in requested_experience_ids
            if source_id in experience_by_id
        )

        available_normative = _available_normative_sources(
            finding=finding,
            normative_candidates=(normative_candidates),
        )

        if "normative_source_ids" in item:
            requested_normative_ids = string_tuple(
                item.get(
                    "normative_source_ids",
                ),
                limit=3,
            )

            selected_normative = _dedupe_normative_sources(
                tuple(
                    available_normative[source_id]
                    for source_id in requested_normative_ids
                    if source_id in available_normative
                )
            )
        else:
            # Backward-compatible fallback для старых fake responses
            # и возможного in-flight legacy ответа.
            selected_normative = finding.basis_sources

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
            recommendation = _fallback_recommendation(
                finding,
            )

        finding_category = finding.category

        normalized_status = finding.status

        has_customer_basis = bool(
            finding.technical_assignment_basis_sources
            or finding.user_package_basis_sources
        )

        if not selected_normative:
            if finding_category == "normative_control":
                finding_category = (
                    "customer_requirements" if has_customer_basis else "other"
                )

            if not has_customer_basis:
                normalized_status = "needs_review"

        return FinalFinding(
            finding_id=finding.finding_id,
            page=finding.page,
            page_type=finding.page_type,
            category=finding_category,
            severity=finding.severity,
            status=normalized_status,
            comment=comment,
            evidence=finding.evidence,
            recommendation=recommendation,
            confidence=finding.confidence,
            basis=build_basis(
                selected_normative,
            ),
            basis_sources=selected_normative,
            experience_sources=selected_experience,
            technical_assignment_basis_sources=(
                finding.technical_assignment_basis_sources
            ),
            user_package_basis_sources=(finding.user_package_basis_sources),
        )
