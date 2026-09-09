# services/knowledge-service/src/pdrd_knowledge_service/domain/technical_assignment_requirements.py

"""Domain read-model atomic requirements технического задания."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

TechnicalAssignmentRequirementStrength = Literal[
    "explicit",
    "candidate",
]


@dataclass(frozen=True, slots=True)
class TechnicalAssignmentRequirementSource:
    """Один persisted atomic requirement из T collection."""

    point_id: str

    requirement_id: str

    requirement_index: int

    technical_assignment_id: str

    analysis_document_id: str

    section_id: str

    source_file: str | None

    source_sha256: str | None

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
class TechnicalAssignmentRequirementList:
    """Deterministic page atomic requirements одного ТЗ."""

    technical_assignment_id: UUID

    analysis_document_id: UUID

    section_id: UUID

    source_file: str

    source_sha256: str

    total: int

    offset: int

    limit: int

    requirements: tuple[
        TechnicalAssignmentRequirementSource,
        ...,
    ]
