# services/analysis-service/src/pdrd_analysis_service/domain/technical_assignment_validation.py

"""Domain-модели независимой T-first проверки технического задания."""

from dataclasses import dataclass
from typing import Literal

from pdrd_analysis_service.domain.analysis import (
    FindingSeverity,
)

TechnicalAssignmentRequirementStrength = Literal[
    "explicit",
    "candidate",
]

TechnicalAssignmentDecisionStatus = Literal[
    "not_applicable",
    "satisfied",
    "violated",
    "insufficient_evidence",
]


@dataclass(frozen=True, slots=True)
class TechnicalAssignmentRequirement:
    """Atomic requirement из deterministic T-R feed."""

    point_id: str

    requirement_id: str

    requirement_index: int

    page: int

    strength: TechnicalAssignmentRequirementStrength

    scopes: tuple[
        str,
        ...,
    ]

    normative_refs: tuple[
        str,
        ...,
    ]

    source_text: str

    text: str


@dataclass(frozen=True, slots=True)
class TechnicalAssignmentDecision:
    """Результат проверки одного atomic T requirement по листу."""

    requirement_id: str

    status: TechnicalAssignmentDecisionStatus

    severity: FindingSeverity

    comment: str

    evidence: str

    recommendation_draft: str

    confidence: float
