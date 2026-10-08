# services/analysis-service/src/pdrd_analysis_service/domain/analysis.py

"""Модели предметной области визуального анализа."""

from dataclasses import dataclass, field
from typing import (
    Any,
    Literal,
)

from pdrd_analysis_service.domain.document_context import DocumentFact

FindingCategory = Literal[
    "normative_control",
    "equipment",
    "scheme_logic",
    "marking",
    "completeness",
    "optimization",
    "customer_requirements",
    "document_consistency",
    "other",
]

FindingSeverity = Literal[
    "info",
    "warning",
    "error",
]

FindingStatus = Literal[
    "confirmed",
    "needs_review",
    "hypothesis",
]


@dataclass(frozen=True, slots=True)
class GenerationMetrics:
    """Метрики одного structured VLM-вызова."""

    attempt: int
    done_reason: str | None

    requested_num_predict: int

    total_duration_ms: float
    load_duration_ms: float

    prompt_eval_count: int | None
    eval_count: int | None

    content_length: int
    thinking_length: int

    def as_dict(
        self,
    ) -> dict[
        str,
        Any,
    ]:
        """Возвращает transport-friendly представление."""
        return {
            "attempt": self.attempt,
            "done_reason": self.done_reason,
            "requested_num_predict": self.requested_num_predict,
            "total_duration_ms": self.total_duration_ms,
            "load_duration_ms": self.load_duration_ms,
            "prompt_eval_count": self.prompt_eval_count,
            "eval_count": self.eval_count,
            "content_length": self.content_length,
            "thinking_length": self.thinking_length,
        }


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """Structured JSON и метрики VLM."""

    payload: dict[
        str,
        Any,
    ]

    metrics: GenerationMetrics


@dataclass(frozen=True, slots=True)
class PageFacts:
    """Объективные факты, извлечённые из одного листа."""

    discipline: str
    page_type: str
    summary: str

    objects: tuple[
        str,
        ...,
    ]

    connections: tuple[
        str,
        ...,
    ]

    labels: tuple[
        str,
        ...,
    ]

    normative_queries: tuple[
        str,
        ...,
    ]

    document_facts: tuple[DocumentFact, ...] = ()
    equipment_identities: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class FindingVisualRegion:
    """Визуальная evidence-область finding в координатах 0..1000."""

    x_min: int
    y_min: int
    x_max: int
    y_max: int

    confidence: float

    label: str = ""

    def __post_init__(
        self,
    ) -> None:
        """Проверяет границы и геометрию visual region."""
        coordinates = (
            self.x_min,
            self.y_min,
            self.x_max,
            self.y_max,
        )

        if any(coordinate < 0 or coordinate > 1000 for coordinate in coordinates):
            raise ValueError(
                "Visual region coordinates должны быть в диапазоне 0..1000.",
            )

        if self.x_min >= self.x_max or self.y_min >= self.y_max:
            raise ValueError(
                "Visual region должна иметь положительную площадь.",
            )

        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                "Visual region confidence должен быть в диапазоне 0..1.",
            )

    def as_dict(
        self,
    ) -> dict[
        str,
        Any,
    ]:
        """Возвращает transport-friendly visual region."""
        return {
            "x_min": self.x_min,
            "y_min": self.y_min,
            "x_max": self.x_max,
            "y_max": self.y_max,
            "confidence": self.confidence,
            "label": self.label,
        }


@dataclass(frozen=True, slots=True)
class DocumentContextSource:
    """Сохранённый фрагмент проверяемого PDF, не нормативное основание."""

    source_id: str
    page: int
    evidence_text: str = ""
    text: str = ""
    fact_id: str | None = None
    chunk_index: int | None = None
    score: float = 0.0
    match_type: str = ""
    subject: str = ""
    property: str = ""
    scope: str = ""
    visual_regions: tuple[FindingVisualRegion, ...] = ()

    def __post_init__(self) -> None:
        """Запрещает ссылки без физической страницы и стабильной идентичности."""
        if self.page < 1 or not self.source_id.startswith(f"D-p{self.page:04d}-"):
            raise ValueError("Источник D должен содержать физическую страницу в ID.")


@dataclass(frozen=True, slots=True)
class EquipmentDocumentationSource:
    """Конкретный snapshot документа производителя для EQ-замечания."""

    source_id: str
    manufacturer: str
    model: str
    variant: str
    property_name: str
    value_raw: str
    unit_raw: str
    page: int
    snippet: str
    document_revision: str
    sha256: str
    source_url: str
    trust_status: str


def saved_source_kinds(
    *, document=(), normative=(), technical=(), user=(), experience=(), equipment=()
) -> tuple[str, ...]:
    """Вычисляет типы источников только из сохранённых оснований."""
    return tuple(
        kind
        for kind, sources in (
            ("D", document),
            ("N", normative),
            ("T", technical),
            ("U", user),
            ("E", experience),
            ("EQ", equipment),
        )
        if sources
    )


@dataclass(frozen=True, slots=True)
class NormativeSource:
    """Нормативный фрагмент, полученный из Knowledge Service."""

    source_id: str
    point_id: str
    score: float

    source_file: str | None
    source_path: str | None

    page: int | str | None
    chunk_index: int | str | None

    text: str

    document_id: str | None = None
    section_id: str | None = None
    category_id: str | None = None
    source_sha256: str | None = None


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
    ] = ()


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
class UserPackageSource:
    """Фрагмент пользовательского документа для contextual analysis."""

    source_id: str
    point_id: str
    score: float

    source_file: str | None
    source_path: str | None

    page: int | str | None
    chunk_index: int | str | None

    text: str

    document_id: str | None = None
    section_id: str | None = None
    category_id: str | None = None
    source_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ExperienceSource:
    """Пример из Базы Опыта."""

    source_id: str
    point_id: str
    score: float

    project_id: str | None
    issue_id: str | None
    issue_text: str | None

    status: str | None
    verified_fixed: bool

    before_page: int | str | None
    after_page: int | str | None

    before_context: str
    after_context: str
    example_id: str | None = None
    example_revision: int | None = None
    tag: str | None = None
    decision: str | None = None
    learning_use: str | None = None
    negative_target: str | None = None


@dataclass(frozen=True, slots=True)
class FindingDraft:
    """Нарушение после проверки требований, до финализации."""

    finding_id: str

    page: int
    page_type: str

    category: FindingCategory
    severity: FindingSeverity
    status: FindingStatus

    comment: str
    evidence: str
    recommendation_draft: str

    confidence: float

    normative_source_ids: tuple[
        str,
        ...,
    ]

    basis: str

    basis_sources: tuple[
        NormativeSource,
        ...,
    ]

    experience_query: str

    technical_assignment_source_ids: tuple[
        str,
        ...,
    ] = ()

    technical_assignment_basis_sources: tuple[
        TechnicalAssignmentSource,
        ...,
    ] = ()

    user_package_source_ids: tuple[
        str,
        ...,
    ] = ()

    user_package_basis_sources: tuple[
        UserPackageSource,
        ...,
    ] = ()

    visual_regions: tuple[
        FindingVisualRegion,
        ...,
    ] = ()

    origin_assertions: tuple[dict[str, Any], ...] = ()

    object_ref: str = ""
    evidence_locations: tuple[dict[str, Any], ...] = ()
    document_context_source_ids: tuple[str, ...] = ()
    document_context_basis_sources: tuple[DocumentContextSource, ...] = ()
    equipment_documentation_source_ids: tuple[str, ...] = ()
    equipment_documentation_basis_sources: tuple[EquipmentDocumentationSource, ...] = ()
    equipment_details: dict[str, Any] = field(default_factory=dict)

    @property
    def source_kinds(self) -> tuple[str, ...]:
        """Возвращает происхождение замечания без доверия ответу модели."""
        return saved_source_kinds(
            document=self.document_context_basis_sources,
            equipment=self.equipment_documentation_basis_sources,
            normative=self.basis_sources,
            technical=self.technical_assignment_basis_sources,
            user=self.user_package_basis_sources,
            experience=getattr(self, "experience_sources", ()),
        )


@dataclass(frozen=True, slots=True)
class FinalFinding:
    """Итоговое замечание Analysis Service."""

    finding_id: str

    page: int
    page_type: str

    category: FindingCategory
    severity: FindingSeverity
    status: FindingStatus

    comment: str
    evidence: str
    recommendation: str

    confidence: float

    basis: str

    basis_sources: tuple[
        NormativeSource,
        ...,
    ]

    experience_sources: tuple[
        ExperienceSource,
        ...,
    ]

    technical_assignment_basis_sources: tuple[
        TechnicalAssignmentSource,
        ...,
    ] = ()

    user_package_basis_sources: tuple[
        UserPackageSource,
        ...,
    ] = ()

    origin_assertions: tuple[dict[str, Any], ...] = ()

    object_ref: str = ""
    evidence_locations: tuple[dict[str, Any], ...] = ()
    document_context_source_ids: tuple[str, ...] = ()
    document_context_basis_sources: tuple[DocumentContextSource, ...] = ()
    equipment_documentation_source_ids: tuple[str, ...] = ()
    equipment_documentation_basis_sources: tuple[EquipmentDocumentationSource, ...] = ()
    equipment_details: dict[str, Any] = field(default_factory=dict)

    @property
    def source_kinds(self) -> tuple[str, ...]:
        """Возвращает происхождение замечания без доверия ответу модели."""
        return saved_source_kinds(
            document=self.document_context_basis_sources,
            equipment=self.equipment_documentation_basis_sources,
            normative=self.basis_sources,
            technical=self.technical_assignment_basis_sources,
            user=self.user_package_basis_sources,
            experience=getattr(self, "experience_sources", ()),
        )


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    """Готовность сервиса анализа."""

    vision_model: bool

    @property
    def ready(
        self,
    ) -> bool:
        """Возвращает готовность сервиса."""
        return self.vision_model
