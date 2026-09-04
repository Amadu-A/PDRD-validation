# services/analysis-service/src/pdrd_analysis_service/domain/analysis.py

"""Domain-модели VLM-анализа."""

from dataclasses import dataclass
from typing import (
    Any,
    Literal,
)

FindingCategory = Literal[
    "normative_control",
    "equipment",
    "scheme_logic",
    "marking",
    "completeness",
    "optimization",
    "customer_requirements",
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

    user_package_source_ids: tuple[
        str,
        ...,
    ] = ()

    user_package_basis_sources: tuple[
        UserPackageSource,
        ...,
    ] = ()


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

    user_package_basis_sources: tuple[
        UserPackageSource,
        ...,
    ] = ()


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    """Readiness Analysis Service."""

    vision_model: bool

    @property
    def ready(
        self,
    ) -> bool:
        """Возвращает готовность сервиса."""
        return self.vision_model
