# services/knowledge-service/src/pdrd_knowledge_service/domain/search.py

"""Domain-модели поиска по базе знаний."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class VectorPoint:
    """Результат vector search без зависимости от Qdrant."""

    point_id: str
    score: float
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class NormativeSource:
    """Нормативный источник, найденный для проверки."""

    source_id: str
    point_id: str
    score: float

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
class ExperienceSearchResult:
    """Результат поиска опыта для одного нарушения."""

    query: str
    sources: tuple[ExperienceSource, ...]
    embedding_model: str


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    """Состояние внешних зависимостей Knowledge Service."""

    embedding_model: bool
    qdrant: bool

    normative_collection: bool
    experience_collection: bool

    @property
    def ready(self) -> bool:
        """Возвращает полную готовность Knowledge Service."""
        return all(
            (
                self.embedding_model,
                self.qdrant,
                self.normative_collection,
                self.experience_collection,
            )
        )
