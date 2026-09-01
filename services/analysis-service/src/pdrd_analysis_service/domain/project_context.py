# services/analysis-service/src/pdrd_analysis_service/domain/project_context.py

"""Domain-модели контекста Пояснительной записки."""

from dataclasses import dataclass
from enum import StrEnum


class InvalidProjectContextError(ValueError):
    """Выбранный пользователем контекст ПЗ невалиден."""


class ProjectContextPageKind(StrEnum):
    """Тип страницы выбранного пользователем диапазона."""

    EXPLANATORY_NOTE = "explanatory_note"

    DRAWING = "drawing"
    SPECIFICATION = "specification"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class ProjectContextPage:
    """Text-only страница диапазона ПЗ."""

    page_number: int
    text: str


@dataclass(frozen=True, slots=True)
class ProjectContextClassification:
    """Результат классификации одной страницы."""

    page_number: int

    kind: ProjectContextPageKind

    confidence: float

    reason: str


@dataclass(frozen=True, slots=True)
class ProjectContextValidation:
    """Результат проверки диапазона ПЗ."""

    enabled: bool

    pages_count: int

    classifications: tuple[
        ProjectContextClassification,
        ...,
    ]

    warnings: tuple[
        ProjectContextClassification,
        ...,
    ]


@dataclass(frozen=True, slots=True)
class ProjectContextSource:
    """Один найденный semantic фрагмент ПЗ."""

    source_id: str

    score: float

    page: int | None

    chunk_index: int | None

    text: str


@dataclass(frozen=True, slots=True)
class ProjectContextAugmentation:
    """Результат добавления ПЗ к тексту проверки."""

    analysis_text: str

    project_context_texts: tuple[
        str,
        ...,
    ]

    sources: tuple[
        ProjectContextSource,
        ...,
    ]
