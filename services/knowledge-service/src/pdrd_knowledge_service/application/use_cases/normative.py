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
    CatalogArea,
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
        expected_area: CatalogArea = CatalogArea.NORMATIVE,
        source_prefix: str = "N",
        query_instruction: str = NORMATIVE_QUERY_INSTRUCTION,
        allow_unscoped: bool = True,
    ) -> NormativeSearchResult:
        """Выполняет embedding и managed scoped vector search."""
        scope = await self._resolve_scope(
            section_id=section_id,
            document_ids=document_ids,
            expected_area=expected_area,
            allow_unscoped=allow_unscoped,
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

        if not allow_unscoped and scope is None:
            return NormativeSearchResult(
                queries=normalized_queries,
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
            instruction=query_instruction,
        )

        search_filter = None

        if scope is not None:
            search_filter = self._build_scope_filter(
                scope,
            )

        elif (
            expected_area is CatalogArea.NORMATIVE
            and self.unit_of_work_factory is not None
        ):
            ready_document_ids = await self._list_ready_document_ids(
                area=CatalogArea.NORMATIVE,
            )

            if not ready_document_ids:
                return NormativeSearchResult(
                    queries=normalized_queries,
                    sources=(),
                    embedding_model=self.embedding_model,
                )

            search_filter = self._build_document_filter(
                ready_document_ids,
            )

        if search_filter is None:
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
                source_prefix=source_prefix,
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
        expected_area: CatalogArea,
        allow_unscoped: bool,
    ) -> NormativeSearchScope | None:
        """Валидирует selection snapshot против managed PostgreSQL catalog."""
        if section_id is None and document_ids is None:
            if allow_unscoped:
                return None

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

            if expected_area is CatalogArea.NORMATIVE:
                raise NormativeSearchScopeError(
                    f"Выбранные нормативные документы не найдены: {missing_text}.",
                )

            raise NormativeSearchScopeError(
                f"Выбранные пользовательские документы не найдены: {missing_text}.",
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

            if expected_area is CatalogArea.NORMATIVE:
                raise NormativeSearchScopeError(
                    "Документы принадлежат другому нормативному разделу: "
                    f"{foreign_text}.",
                )

            raise NormativeSearchScopeError(
                "Пользовательские документы принадлежат другому разделу: "
                f"{foreign_text}.",
            )

        wrong_area_ids = [
            document.document_id
            for document in documents
            if document.area is not expected_area
        ]

        if wrong_area_ids:
            wrong_area_text = ", ".join(
                str(
                    document_id,
                )
                for document_id in wrong_area_ids
            )

            raise NormativeSearchScopeError(
                f"Документы принадлежат другой области каталога: {wrong_area_text}.",
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

            if expected_area is CatalogArea.NORMATIVE:
                raise NormativeSearchScopeConflictError(
                    "Документы ещё не готовы к нормативному поиску: "
                    f"{unavailable_text}.",
                )

            raise NormativeSearchScopeConflictError(
                "Пользовательские документы ещё не готовы к поиску: "
                f"{unavailable_text}.",
            )

        return NormativeSearchScope(
            section_id=section_id,
            document_ids=normalized_document_ids,
        )

    async def _list_ready_document_ids(
        self,
        *,
        area: CatalogArea,
    ) -> tuple[
        UUID,
        ...,
    ]:
        """Возвращает READY document IDs заданной catalog area."""
        if self.unit_of_work_factory is None:
            return ()

        async with self.unit_of_work_factory() as unit_of_work:
            sections = await unit_of_work.sections.list_all()

            result: list[UUID] = []

            for section in sections:
                documents = await unit_of_work.documents.list_by_section(
                    section.section_id,
                )

                result.extend(
                    document.document_id
                    for document in documents
                    if (
                        document.area is area
                        and document.index_status is IndexingStatus.READY
                    )
                )

        return tuple(
            dict.fromkeys(
                result,
            )
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
        """Строит vector filter section + exact document IDs."""
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
    def _build_document_filter(
        document_ids: tuple[
            UUID,
            ...,
        ],
    ) -> VectorSearchFilter:
        """Строит catalog-wide filter по exact managed document IDs."""
        return VectorSearchFilter(
            must=(
                VectorSearchCondition(
                    key="document_id",
                    values=tuple(
                        str(
                            document_id,
                        )
                        for document_id in document_ids
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
        by_id: dict[
            str,
            VectorPoint,
        ] = {}

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
        source_prefix: str,
    ) -> NormativeSource:
        """Преобразует vector payload в managed source."""
        payload = point.payload

        return NormativeSource(
            source_id=f"{source_prefix}{index}",
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
            (
                int,
                str,
            ),
        ):
            return value

        return None
