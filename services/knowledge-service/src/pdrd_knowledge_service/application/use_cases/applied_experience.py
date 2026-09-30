# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/applied_experience.py

"""Рабочий поиск использует применённую проверенную версию только своего раздела.

Назначение читается перед поиском и повторно перед выдачей результата. Отзыв,
замена либо удаление версии во время ожидания GPU исключают прежний контекст.
"""

from dataclasses import dataclass, replace

from pdrd_knowledge_service.application.ports.experience_versions import (
    AppliedExperienceVersions,
)
from pdrd_knowledge_service.application.ports.vector_store import VectorStoreError
from pdrd_knowledge_service.application.use_cases.index_experience_version import (
    SelectedExperienceFeed,
)
from pdrd_knowledge_service.application.use_cases.trusted_experience import (
    SearchTrustedExperience,
)
from pdrd_knowledge_service.domain.search import ExperienceSearchResult


@dataclass(frozen=True, slots=True)
class SearchAppliedExperience:
    """Не подменяет отсутствие назначения прежним индексом или соседним разделом."""

    search: SearchTrustedExperience
    versions: AppliedExperienceVersions
    dimension: int
    enabled: bool = False

    async def execute(
        self, queries: list[str], *, section_id: str | None = None
    ) -> tuple[ExperienceSearchResult, ...]:
        """Выключенный режим и отсутствие раздела не делают сетевых либо GPU вызовов."""
        normalized = tuple(query.strip() for query in queries)
        if any(not query for query in normalized):
            raise ValueError("Запрос E не может быть пустым.")
        empty = tuple(
            ExperienceSearchResult(query, (), self.search.embedding_model)
            for query in normalized
        )
        if not self.enabled or not normalized or not section_id:
            return empty
        options = {
            "section_id": section_id,
            "model": self.search.embedding_model,
            "identity": self.search.identity,
            "dimension": self.dimension,
            "top_k": self.search.top_k,
            "min_score": self.search.min_score,
        }
        applied = await self.versions.applied(**options)
        if applied is None:
            return empty
        if not await self.search.vector_store.collection_exists(applied.job.collection):
            raise VectorStoreError(
                "Коллекция применённой версии E отсутствует в Qdrant."
            )
        search = replace(
            self.search,
            collection=applied.job.collection,
            source=SelectedExperienceFeed(self.search.source, applied.job.members),
            require_section=True,
            enabled=True,
        )
        results = await search.execute(list(normalized), section_id=section_id)
        current = await self.versions.applied(**options)
        return results if applied == current else empty
