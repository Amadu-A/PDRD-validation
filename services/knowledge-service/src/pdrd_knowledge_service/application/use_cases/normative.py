# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/normative.py

"""Поиск нормативных требований."""

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pdrd_knowledge_service.application.ports.embedding import (
    EmbeddingProvider,
)
from pdrd_knowledge_service.application.ports.persistence import (
    NormativeCatalogUnitOfWorkFactory,
)
from pdrd_knowledge_service.application.ports.vector_store import (
    VectorStore,
)
from pdrd_knowledge_service.application.use_cases.normative_sections import (
    NormativeSectionNotFoundError,
)
from pdrd_knowledge_service.domain.normative_catalog import (
    IndexingStatus,
)
from pdrd_knowledge_service.domain.search import (
    NormativeSearchResult,
    NormativeSearchScope,
    NormativeSource,
    VectorPoint,
    VectorSearchCondition,
    VectorSearchFilter,
)

NORMATIVE_QUERY_INSTRUCTION = (
    "Given a description of a Russian engineering drawing or a technical "
    "check topic, retrieve the most directly applicable normative requirement "
    "for compliance verification."
)


class NormativeSearchScopeError(ValueError):
    """Некорректный набор нормативных документов для retrieval."""


class NormativeSearchScopeConflictError(RuntimeError):
    """Выбранный нормативный документ ещё не готов к retrieval."""


@dataclass(frozen=True, slots=True)
class SearchNormative:
    """Ищет нормативы для набора тем проверки."""

    embedding_provider: EmbeddingProvider
    vector_store: VectorStore

    collection: str
    embedding_model: str

    top_k: int
    max_sources: int

    unit_of_work_factory: NormativeCatalogUnitOfWorkFactory | None = None

    async def execute(
        self,
        queries: list[str],
        *,
        section_id: UUID | None = None,
        document_ids: list[UUID] | None = None,
    ) -> NormativeSearchResult:
        """Выполняет embedding и scoped vector search нормативов."""
        scope = await self._resolve_scope(
            section_id=section_id,
            document_ids=document_ids,
        )

        normalized_queries = self._normalize_queries(
            queries,
        )

        if not normalized_queries:
            return NormativeSearchResult(
                queries=(),
                sources=(),
                embedding_model=self.embedding_model,
            )

        if scope is not None and not scope.document_ids:
            return NormativeSearchResult(
                queries=normalized_queries,
                sources=(),
                embedding_model=self.embedding_model,
            )

        vectors = await self.embedding_provider.embed(
            normalized_queries,
            instruction=NORMATIVE_QUERY_INSTRUCTION,
        )

        if scope is None:
            groups = await asyncio.gather(
                *(
                    self.vector_store.search(
                        collection=self.collection,
                        vector=vector,
                        limit=self.top_k,
                    )
                    for vector in vectors
                )
            )

        else:
            search_filter = self._build_scope_filter(
                scope,
            )

            groups = await asyncio.gather(
                *(
                    self.vector_store.search_filtered(
                        collection=self.collection,
                        vector=vector,
                        limit=self.top_k,
                        search_filter=search_filter,
                    )
                    for vector in vectors
                )
            )

        merged = self._merge_points(
            groups,
        )

        sources = tuple(
            self._build_source(
                point,
                index=index,
            )
            for index, point in enumerate(
                merged,
                start=1,
            )
        )

        return NormativeSearchResult(
            queries=normalized_queries,
            sources=sources,
            embedding_model=self.embedding_model,
        )

    async def _resolve_scope(
        self,
        *,
        section_id: UUID | None,
        document_ids: list[UUID] | None,
    ) -> NormativeSearchScope | None:
        """Валидирует selection snapshot против managed PostgreSQL catalog."""
        if section_id is None and document_ids is None:
            return None

        if section_id is None or document_ids is None:
            raise NormativeSearchScopeError(
                "section_id и document_ids должны передаваться вместе.",
            )

        if self.unit_of_work_factory is None:
            raise NormativeSearchScopeError(
                "Managed normative catalog persistence не настроен.",
            )

        normalized_document_ids = self._normalize_document_ids(
            document_ids,
        )

        async with self.unit_of_work_factory() as unit_of_work:
            section = await unit_of_work.sections.get(
                section_id,
            )

            if section is None:
                raise NormativeSectionNotFoundError(
                    f"Раздел нормативной базы {section_id} не найден.",
                )

            documents = await unit_of_work.documents.list_by_ids(
                normalized_document_ids,
            )

        documents_by_id = {document.document_id: document for document in documents}

        missing_ids = [
            document_id
            for document_id in normalized_document_ids
            if document_id not in documents_by_id
        ]

        if missing_ids:
            missing_text = ", ".join(
                str(
                    document_id,
                )
                for document_id in missing_ids
            )

            raise NormativeSearchScopeError(
                f"Выбранные нормативные документы не найдены: {missing_text}.",
            )

        foreign_ids = [
            document.document_id
            for document in documents
            if document.section_id != section_id
        ]

        if foreign_ids:
            foreign_text = ", ".join(
                str(
                    document_id,
                )
                for document_id in foreign_ids
            )

            raise NormativeSearchScopeError(
                f"Документы принадлежат другому нормативному разделу: {foreign_text}.",
            )

        unavailable_ids = [
            document.document_id
            for document in documents
            if document.index_status is not IndexingStatus.READY
        ]

        if unavailable_ids:
            unavailable_text = ", ".join(
                str(
                    document_id,
                )
                for document_id in unavailable_ids
            )

            raise NormativeSearchScopeConflictError(
                f"Документы ещё не готовы к нормативному поиску: {unavailable_text}.",
            )

        return NormativeSearchScope(
            section_id=section_id,
            document_ids=normalized_document_ids,
        )

    @staticmethod
    def _normalize_document_ids(
        document_ids: list[UUID],
    ) -> tuple[
        UUID,
        ...,
    ]:
        """Удаляет duplicate document IDs, сохраняя порядок selection."""
        result: list[UUID] = []

        seen: set[UUID] = set()

        for document_id in document_ids:
            if document_id in seen:
                continue

            seen.add(
                document_id,
            )

            result.append(
                document_id,
            )

        return tuple(
            result,
        )

    @staticmethod
    def _build_scope_filter(
        scope: NormativeSearchScope,
    ) -> VectorSearchFilter:
        """Строит generic vector filter для managed payload."""
        return VectorSearchFilter(
            must=(
                VectorSearchCondition(
                    key="section_id",
                    values=(
                        str(
                            scope.section_id,
                        ),
                    ),
                ),
                VectorSearchCondition(
                    key="document_id",
                    values=tuple(
                        str(
                            document_id,
                        )
                        for document_id in scope.document_ids
                    ),
                ),
            )
        )

    @staticmethod
    def _normalize_queries(
        queries: list[str],
    ) -> tuple[str, ...]:
        """Нормализует и дедуплицирует retrieval queries."""
        result: list[str] = []
        seen: set[str] = set()

        for query in queries:
            normalized = query.strip()

            if not normalized or normalized in seen:
                continue

            seen.add(
                normalized,
            )

            result.append(
                normalized,
            )

        return tuple(
            result,
        )

    def _merge_points(
        self,
        groups: tuple[
            list[VectorPoint],
            ...,
        ],
    ) -> list[VectorPoint]:
        """Дедуплицирует Qdrant points и оставляет лучший score."""
        by_id: dict[str, VectorPoint] = {}

        for points in groups:
            for point in points:
                if not point.point_id:
                    continue

                previous = by_id.get(
                    point.point_id,
                )

                if previous is None or point.score > previous.score:
                    by_id[point.point_id] = point

        return sorted(
            by_id.values(),
            key=lambda point: point.score,
            reverse=True,
        )[: self.max_sources]

    @staticmethod
    def _build_source(
        point: VectorPoint,
        *,
        index: int,
    ) -> NormativeSource:
        """Преобразует vector payload в нормативный источник."""
        payload = point.payload

        return NormativeSource(
            source_id=f"N{index}",
            point_id=point.point_id,
            score=round(
                point.score,
                4,
            ),
            document_id=SearchNormative._optional_string(
                payload.get(
                    "document_id",
                )
            ),
            section_id=SearchNormative._optional_string(
                payload.get(
                    "section_id",
                )
            ),
            category_id=SearchNormative._optional_string(
                payload.get(
                    "category_id",
                )
            ),
            source_sha256=SearchNormative._optional_string(
                payload.get(
                    "source_sha256",
                )
            ),
            source_file=SearchNormative._optional_string(
                payload.get(
                    "source_file",
                )
            ),
            source_path=SearchNormative._optional_string(
                payload.get(
                    "source_path",
                )
            ),
            page=SearchNormative._page_value(
                payload.get(
                    "page",
                )
            ),
            chunk_index=SearchNormative._page_value(
                payload.get(
                    "chunk_index",
                )
            ),
            text=str(
                payload.get(
                    "text",
                    "",
                )
                or ""
            ),
        )

    @staticmethod
    def _optional_string(
        value: Any,
    ) -> str | None:
        """Преобразует optional payload value в string."""
        if value is None:
            return None

        return str(
            value,
        )

    @staticmethod
    def _page_value(
        value: Any,
    ) -> int | str | None:
        """Нормализует page/chunk payload value."""
        if isinstance(
            value,
            (int, str),
        ):
            return value

        return None
