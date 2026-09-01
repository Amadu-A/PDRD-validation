# services/analysis-service/src/pdrd_analysis_service/application/use_cases/finalization.py

"""Use case финализации нормативных findings."""

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
    string_tuple,
)
from pdrd_analysis_service.domain.analysis import (
    ExperienceSource,
    FinalFinding,
    FindingDraft,
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
        """Оформляет findings и использует релевантный опыт."""
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
            for (
                finding_id,
                sources,
            ) in experience_by_finding.items()
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
                "batches": (batch_metrics),
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
            finding_id=(finding.finding_id),
            page=finding.page,
            page_type=(finding.page_type),
            category=(finding.category),
            severity=(finding.severity),
            status=(finding.status),
            comment=(finding.comment),
            evidence=(finding.evidence),
            recommendation=(recommendation),
            confidence=(finding.confidence),
            basis=(finding.basis),
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
        experience_by_id = {source.source_id: source for source in available_experience}

        requested_ids = string_tuple(
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
            finding_id=(finding.finding_id),
            page=finding.page,
            page_type=(finding.page_type),
            category=(finding.category),
            severity=(finding.severity),
            status=(finding.status),
            comment=comment,
            evidence=(finding.evidence),
            recommendation=(recommendation),
            confidence=(finding.confidence),
            basis=(finding.basis),
            basis_sources=(finding.basis_sources),
            experience_sources=(selected_experience),
        )
