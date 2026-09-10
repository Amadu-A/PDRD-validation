# services/analysis-service/src/pdrd_analysis_service/application/use_cases/technical_assignment_validation.py

"""Use case независимой T-first проверки atomic requirements."""

import logging
from dataclasses import dataclass
from typing import (
    Any,
    cast,
)

from pdrd_analysis_service.application.ports.vision_model import (
    StructuredVisionModel,
)
from pdrd_analysis_service.application.prompts import (
    build_experience_query,
)
from pdrd_analysis_service.application.technical_assignment_prompt import (
    build_technical_assignment_check_prompt,
)
from pdrd_analysis_service.application.technical_assignment_schema import (
    TECHNICAL_ASSIGNMENT_DECISION_STATUSES,
    build_technical_assignment_check_schema,
)
from pdrd_analysis_service.domain.analysis import (
    FindingDraft,
    FindingSeverity,
    GenerationMetrics,
    PageFacts,
    TechnicalAssignmentSource,
)
from pdrd_analysis_service.domain.technical_assignment_validation import (
    TechnicalAssignmentDecision,
    TechnicalAssignmentDecisionStatus,
    TechnicalAssignmentRequirement,
)

logger = logging.getLogger(
    __name__,
)

_ALLOWED_SEVERITIES = {
    "info",
    "warning",
    "error",
}

_FINDING_DECISION_STATUSES = {
    "violated",
    "insufficient_evidence",
}


class TechnicalAssignmentValidationError(
    RuntimeError,
):
    """VLM не вернула lossless T-first decisions."""


@dataclass(frozen=True, slots=True)
class CheckPageAgainstTechnicalAssignment:
    """Проверяет лист по всем переданным atomic T requirements."""

    vision_model: StructuredVisionModel

    num_predict: int

    batch_size: int

    requirement_text_limit: int

    async def execute(
        self,
        *,
        page_number: int,
        page_type: str,
        extracted_text: str,
        page_facts: PageFacts,
        image_bytes: bytes,
        technical_assignment_id: str,
        analysis_document_id: str,
        section_id: str,
        source_file: str,
        source_sha256: str,
        requirements: tuple[
            TechnicalAssignmentRequirement,
            ...,
        ],
    ) -> tuple[
        str,
        tuple[
            TechnicalAssignmentDecision,
            ...,
        ],
        tuple[
            FindingDraft,
            ...,
        ],
        tuple[
            GenerationMetrics,
            ...,
        ],
    ]:
        """Выполняет independent exhaustive T-first validation."""
        self._validate_requirements(
            requirements,
        )

        decisions: list[TechnicalAssignmentDecision] = []

        metrics: list[GenerationMetrics] = []

        for batch_number, start in enumerate(
            range(
                0,
                len(
                    requirements,
                ),
                self.batch_size,
            ),
            start=1,
        ):
            batch = requirements[start : start + self.batch_size]

            requirement_ids = tuple(requirement.requirement_id for requirement in batch)

            result = await self.vision_model.generate_json(
                prompt=(
                    build_technical_assignment_check_prompt(
                        page_number=page_number,
                        extracted_text=extracted_text,
                        page_facts=page_facts,
                        requirements=batch,
                        requirement_text_limit=(self.requirement_text_limit),
                    )
                ),
                schema=(
                    build_technical_assignment_check_schema(
                        requirement_ids,
                    )
                ),
                num_predict=self.num_predict,
                seed=500 + batch_number,
                stage=(f"technical_assignment_check:{page_number}:{batch_number}"),
                image_bytes=image_bytes,
            )

            metrics.append(
                result.metrics,
            )

            batch_decisions = self._parse_batch_decisions(
                payload=result.payload,
                requirements=batch,
            )

            decisions.extend(
                batch_decisions,
            )

        findings: list[FindingDraft] = []

        requirement_by_id = {
            requirement.requirement_id: requirement for requirement in requirements
        }

        for decision in decisions:
            if decision.status not in _FINDING_DECISION_STATUSES:
                continue

            requirement = requirement_by_id[decision.requirement_id]

            findings.append(
                self._build_finding(
                    page_number=page_number,
                    page_type=page_type,
                    decision=decision,
                    requirement=requirement,
                    technical_assignment_id=(technical_assignment_id),
                    analysis_document_id=(analysis_document_id),
                    section_id=section_id,
                    source_file=source_file,
                    source_sha256=source_sha256,
                )
            )

        violated_count = sum(decision.status == "violated" for decision in decisions)

        review_count = sum(
            decision.status == "insufficient_evidence" for decision in decisions
        )

        logger.info(
            (
                "technical_assignment_first_pass "
                "page=%s requirements=%s "
                "batches=%s violated=%s "
                "insufficient_evidence=%s findings=%s"
            ),
            page_number,
            len(
                requirements,
            ),
            len(
                metrics,
            ),
            violated_count,
            review_count,
            len(
                findings,
            ),
        )

        summary = (
            "Проверено требований ТЗ: "
            f"{len(decisions)}; "
            f"нарушений: {violated_count}; "
            "требуют проверки: "
            f"{review_count}."
        )

        return (
            summary,
            tuple(
                decisions,
            ),
            tuple(
                findings,
            ),
            tuple(
                metrics,
            ),
        )

    @staticmethod
    def _validate_requirements(
        requirements: tuple[
            TechnicalAssignmentRequirement,
            ...,
        ],
    ) -> None:
        """Запрещает пустой или неоднозначный T-first input."""
        if not requirements:
            raise ValueError(
                "Для T-first проверки нужен хотя бы один requirement.",
            )

        requirement_ids = [requirement.requirement_id for requirement in requirements]

        if len(
            set(
                requirement_ids,
            )
        ) != len(
            requirement_ids,
        ):
            raise ValueError(
                "Atomic requirements содержат повторяющиеся requirement_id.",
            )

    @classmethod
    def _parse_batch_decisions(
        cls,
        *,
        payload: dict[
            str,
            Any,
        ],
        requirements: tuple[
            TechnicalAssignmentRequirement,
            ...,
        ],
    ) -> tuple[
        TechnicalAssignmentDecision,
        ...,
    ]:
        """Проверяет exact coverage одного VLM batch."""
        raw_decisions = payload.get(
            "decisions",
        )

        if not isinstance(
            raw_decisions,
            dict,
        ):
            raise TechnicalAssignmentValidationError(
                "T-first VLM не вернула объект decisions.",
            )

        expected_ids = tuple(requirement.requirement_id for requirement in requirements)

        actual_ids = tuple(
            str(
                requirement_id,
            )
            for requirement_id in raw_decisions
        )

        if len(
            raw_decisions,
        ) != len(
            expected_ids,
        ) or set(
            actual_ids,
        ) != set(
            expected_ids,
        ):
            raise TechnicalAssignmentValidationError(
                "T-first VLM потеряла или добавила atomic requirement decision.",
            )

        return tuple(
            cls._parse_decision(
                requirement_id=requirement.requirement_id,
                raw=raw_decisions[requirement.requirement_id],
            )
            for requirement in requirements
        )

    @staticmethod
    def _parse_decision(
        *,
        requirement_id: str,
        raw: Any,
    ) -> TechnicalAssignmentDecision:
        """Строго преобразует один structured decision."""
        if not isinstance(
            raw,
            dict,
        ):
            raise TechnicalAssignmentValidationError(
                "T-first decision должен быть объектом.",
            )

        raw_status = raw.get(
            "status",
        )

        if raw_status not in (TECHNICAL_ASSIGNMENT_DECISION_STATUSES):
            raise TechnicalAssignmentValidationError(
                "T-first decision содержит неизвестный status.",
            )

        raw_severity = raw.get(
            "severity",
        )

        if raw_severity not in _ALLOWED_SEVERITIES:
            raise TechnicalAssignmentValidationError(
                "T-first decision содержит неизвестный severity.",
            )

        comment = CheckPageAgainstTechnicalAssignment._required_text(
            raw,
            "comment",
        )

        evidence = CheckPageAgainstTechnicalAssignment._required_text(
            raw,
            "evidence",
        )

        recommendation_draft = str(
            raw.get(
                "recommendation_draft",
                "",
            )
        ).strip()

        raw_confidence = raw.get(
            "confidence",
        )

        if isinstance(
            raw_confidence,
            bool,
        ) or not isinstance(
            raw_confidence,
            (
                int,
                float,
            ),
        ):
            raise TechnicalAssignmentValidationError(
                "T-first decision confidence должен быть числом.",
            )

        normalized_confidence = float(
            raw_confidence,
        )

        if not (0.0 <= normalized_confidence <= 1.0):
            raise TechnicalAssignmentValidationError(
                "T-first decision confidence вне диапазона 0..1.",
            )

        return TechnicalAssignmentDecision(
            requirement_id=requirement_id,
            status=cast(
                TechnicalAssignmentDecisionStatus,
                raw_status,
            ),
            severity=cast(
                FindingSeverity,
                raw_severity,
            ),
            comment=comment,
            evidence=evidence,
            recommendation_draft=(recommendation_draft),
            confidence=normalized_confidence,
        )

    @staticmethod
    def _required_text(
        raw: dict[
            str,
            Any,
        ],
        key: str,
    ) -> str:
        """Читает обязательный непустой generated text."""
        value = raw.get(
            key,
        )

        if not isinstance(
            value,
            str,
        ):
            raise TechnicalAssignmentValidationError(
                f"T-first decision {key} должен быть строкой.",
            )

        normalized = value.strip()

        if not normalized:
            raise TechnicalAssignmentValidationError(
                f"T-first decision {key} не должен быть пустым.",
            )

        return normalized

    @staticmethod
    def _build_finding(
        *,
        page_number: int,
        page_type: str,
        decision: TechnicalAssignmentDecision,
        requirement: TechnicalAssignmentRequirement,
        technical_assignment_id: str,
        analysis_document_id: str,
        section_id: str,
        source_file: str,
        source_sha256: str,
    ) -> FindingDraft:
        """Преобразует violated/review T decision без потери candidate."""
        source = TechnicalAssignmentSource(
            source_id=requirement.requirement_id,
            point_id=requirement.point_id,
            score=0.0,
            technical_assignment_id=(technical_assignment_id),
            analysis_document_id=(analysis_document_id),
            section_id=section_id,
            source_sha256=source_sha256,
            source_file=source_file,
            page=requirement.page,
            text=requirement.text,
            normative_refs=(requirement.normative_refs),
        )

        finding_status = (
            "confirmed" if decision.status == "violated" else "needs_review"
        )

        return FindingDraft(
            finding_id=(f"p{page_number}-tr{requirement.requirement_index}"),
            page=page_number,
            page_type=page_type,
            category="customer_requirements",
            severity=decision.severity,
            status=finding_status,
            comment=decision.comment,
            evidence=decision.evidence,
            recommendation_draft=(decision.recommendation_draft),
            confidence=decision.confidence,
            normative_source_ids=(),
            basis="",
            basis_sources=(),
            experience_query=build_experience_query(
                category="customer_requirements",
                comment=decision.comment,
                evidence=decision.evidence,
                recommendation_draft=(decision.recommendation_draft),
            ),
            technical_assignment_source_ids=(requirement.requirement_id,),
            technical_assignment_basis_sources=(source,),
            user_package_source_ids=(),
            user_package_basis_sources=(),
        )
