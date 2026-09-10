# services/analysis-service/src/pdrd_analysis_service/transport/http/technical_assignment_schemas.py

"""HTTP schemas независимой T-first проверки."""

from typing import (
    Any,
    Literal,
)
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from pdrd_analysis_service.domain.technical_assignment_validation import (
    TechnicalAssignmentRequirement,
)
from pdrd_analysis_service.transport.http.schemas import (
    FindingDraftPayload,
    PageFactsPayload,
)


class TechnicalAssignmentRequirementPayload(
    BaseModel,
):
    """Atomic requirement из Knowledge Service."""

    model_config = ConfigDict(
        extra="forbid",
    )

    point_id: str = Field(
        min_length=1,
    )

    requirement_id: str = Field(
        min_length=1,
    )

    requirement_index: int = Field(
        ge=1,
    )

    page: int = Field(
        ge=1,
    )

    requirement_strength: Literal[
        "explicit",
        "candidate",
    ]

    scopes: list[str] = Field(
        default_factory=list,
    )

    normative_refs: list[str] = Field(
        default_factory=list,
    )

    source_text: str = Field(
        min_length=1,
    )

    text: str = Field(
        min_length=1,
    )

    def to_domain(
        self,
    ) -> TechnicalAssignmentRequirement:
        """Преобразует HTTP payload в domain requirement."""
        return TechnicalAssignmentRequirement(
            point_id=self.point_id,
            requirement_id=self.requirement_id,
            requirement_index=(self.requirement_index),
            page=self.page,
            strength=self.requirement_strength,
            scopes=tuple(
                self.scopes,
            ),
            normative_refs=tuple(
                self.normative_refs,
            ),
            source_text=self.source_text,
            text=self.text,
        )


class CheckTechnicalAssignmentRequest(
    BaseModel,
):
    """Запрос независимой проверки ТЗ по одному листу."""

    model_config = ConfigDict(
        extra="forbid",
    )

    page_number: int = Field(
        ge=1,
    )

    extracted_text: str

    page_facts: PageFactsPayload

    image_base64: str = Field(
        min_length=1,
    )

    technical_assignment_id: UUID

    analysis_document_id: UUID

    section_id: UUID

    source_file: str = Field(
        min_length=1,
    )

    source_sha256: str = Field(
        min_length=64,
        max_length=64,
    )

    requirements: list[TechnicalAssignmentRequirementPayload] = Field(
        min_length=1,
    )


class TechnicalAssignmentDecisionPayload(
    BaseModel,
):
    """HTTP representation одного T-first decision."""

    requirement_id: str

    status: Literal[
        "not_applicable",
        "satisfied",
        "violated",
        "insufficient_evidence",
    ]

    severity: Literal[
        "info",
        "warning",
        "error",
    ]

    comment: str

    evidence: str

    recommendation_draft: str

    confidence: float


class CheckTechnicalAssignmentResponse(
    BaseModel,
):
    """Ответ independent T-first validation."""

    summary: str

    decisions: list[TechnicalAssignmentDecisionPayload]

    findings: list[FindingDraftPayload]

    metrics: list[
        dict[
            str,
            Any,
        ]
    ]
