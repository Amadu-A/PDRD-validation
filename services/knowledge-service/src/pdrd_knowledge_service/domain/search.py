# services/knowledge-service/src/pdrd_knowledge_service/domain/search.py

"""Domain-модели поиска по базе знаний."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class VectorPoint:
    """Результат vector search без зависимости от Qdrant."""

    point_id: str
    score: float
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class VectorSearchCondition:
    """Одно обязательное условие vector search."""

    key: str

    values: tuple[str, ...]

    def __post_init__(
        self,
    ) -> None:
        """Проверяет корректность generic filter condition."""
        if not self.key.strip():
            raise ValueError(
                "Vector search filter key не может быть пустым.",
            )

        if not self.values:
            raise ValueError(
                "Vector search filter condition требует хотя бы одно value.",
            )

        if any(not value for value in self.values):
            raise ValueError(
                "Vector search filter value не может быть пустым.",
            )


@dataclass(frozen=True, slots=True)
class VectorSearchFilter:
    """Набор обязательных условий vector retrieval."""

    must: tuple[
        VectorSearchCondition,
        ...,
    ]

    def __post_init__(
        self,
    ) -> None:
        """Не допускает пустой scoped filter."""
        if not self.must:
            raise ValueError(
                "Vector search filter требует хотя бы одно условие.",
            )


@dataclass(frozen=True, slots=True)
class NormativeSearchScope:
    """Immutable scope одного managed поиска."""

    section_id: UUID

    document_ids: tuple[
        UUID,
        ...,
    ]


@dataclass(frozen=True, slots=True)
class NormativeSource:
    """Нормативный источник, найденный для проверки."""

    source_id: str
    point_id: str
    score: float

    document_id: str | None
    section_id: str | None
    category_id: str | None

    source_sha256: str | None

    source_file: str | None
    source_path: str | None

    page: int | str | None
    chunk_index: int | str | None

    text: str


@dataclass(frozen=True, slots=True)
class UserPackageSource:
    """Фрагмент выбранного пользовательского документа."""

    source_id: str
    point_id: str
    score: float

    document_id: str | None
    section_id: str | None
    category_id: str | None

    source_sha256: str | None

    source_file: str | None
    source_path: str | None

    page: int | str | None
    chunk_index: int | str | None

    text: str


@dataclass(frozen=True, slots=True)
class ExperienceSource:
    """Найденный пример из Базы Опыта."""

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
class NormativeSearchResult:
    """Результат поиска нормативных требований."""

    queries: tuple[str, ...]
    sources: tuple[NormativeSource, ...]
    embedding_model: str


@dataclass(frozen=True, slots=True)
class UserPackageSearchResult:
    """Результат поиска по выбранным пользовательским документам."""

    queries: tuple[str, ...]
    sources: tuple[UserPackageSource, ...]
    embedding_model: str


@dataclass(frozen=True, slots=True)
class ExperienceSearchResult:
    """Результат поиска опыта для одного нарушения."""

    query: str
    sources: tuple[ExperienceSource, ...]
    embedding_model: str


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    """Состояние внешних зависимостей Knowledge Service."""

    database: bool

    embedding_model: bool
    qdrant: bool

    normative_collection: bool
    experience_collection: bool

    @property
    def ready(
        self,
    ) -> bool:
        """Возвращает полную готовность Knowledge Service."""
        return all(
            (
                self.database,
                self.embedding_model,
                self.qdrant,
                self.normative_collection,
                self.experience_collection,
            )
        )
