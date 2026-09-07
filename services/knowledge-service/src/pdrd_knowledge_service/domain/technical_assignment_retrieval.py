# services/knowledge-service/src/pdrd_knowledge_service/domain/technical_assignment_retrieval.py

"""Domain contracts T-guided normative retrieval."""

from dataclasses import dataclass
from enum import StrEnum

from pdrd_knowledge_service.domain.search import (
    NormativeSource,
)


class NormativeReferenceResolutionStatus(
    StrEnum,
):
    """Результат сопоставления ссылки ТЗ с managed N."""

    RESOLVED = "resolved"

    MISSING = "missing"

    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class TechnicalAssignmentSource:
    """Страница ТЗ, найденная multimodal retrieval."""

    source_id: str

    point_id: str

    score: float

    technical_assignment_id: str | None

    analysis_document_id: str | None

    section_id: str | None

    source_sha256: str | None

    source_file: str | None

    page: int | str | None

    text: str

    normative_refs: tuple[
        str,
        ...,
    ]


@dataclass(frozen=True, slots=True)
class TechnicalAssignmentSearchResult:
    """Результат отдельного поиска по T collection."""

    queries: tuple[
        str,
        ...,
    ]

    sources: tuple[
        TechnicalAssignmentSource,
        ...,
    ]

    embedding_model: str


@dataclass(frozen=True, slots=True)
class NormativeReferenceResolution:
    """Сопоставление одной ссылки ТЗ с N snapshot."""

    reference: str

    normalized_reference: str

    status: NormativeReferenceResolutionStatus

    document_ids: tuple[
        str,
        ...,
    ]

    source_files: tuple[
        str,
        ...,
    ]


@dataclass(frozen=True, slots=True)
class RetrievalDiagnostic:
    """Диагностика без генерации неподтверждённых фактов."""

    code: str

    message: str

    source_id: str | None = None

    reference: str | None = None


@dataclass(frozen=True, slots=True)
class TechnicalAssignmentConflictCandidate:
    """T/N pair, которую должен семантически проверить VLM."""

    technical_assignment_source_id: str

    normative_source_ids: tuple[
        str,
        ...,
    ]

    reason: str


@dataclass(frozen=True, slots=True)
class TechnicalAssignmentGuidedSearchResult:
    """Полный результат T-guided normative retrieval."""

    queries: tuple[
        str,
        ...,
    ]

    technical_assignment_sources: tuple[
        TechnicalAssignmentSource,
        ...,
    ]

    reference_resolutions: tuple[
        NormativeReferenceResolution,
        ...,
    ]

    targeted_normative_sources: tuple[
        NormativeSource,
        ...,
    ]

    general_normative_sources: tuple[
        NormativeSource,
        ...,
    ]

    normative_sources: tuple[
        NormativeSource,
        ...,
    ]

    diagnostics: tuple[
        RetrievalDiagnostic,
        ...,
    ]

    conflict_candidates: tuple[
        TechnicalAssignmentConflictCandidate,
        ...,
    ]

    technical_assignment_embedding_model: str

    normative_embedding_model: str
