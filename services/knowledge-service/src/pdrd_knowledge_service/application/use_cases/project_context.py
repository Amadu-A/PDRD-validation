# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/project_context.py

"""Use cases временного Project Context index."""

from contextlib import suppress
from dataclasses import dataclass
from uuid import (
    NAMESPACE_URL,
    UUID,
    uuid5,
)

from pdrd_knowledge_service.application.ports.embedding import (
    EmbeddingProvider,
    EmbeddingProviderError,
)
from pdrd_knowledge_service.application.ports.vector_store import (
    VectorStore,
    VectorStoreError,
)
from pdrd_knowledge_service.domain.project_context import (
    ProjectContextChunk,
    ProjectContextError,
    ProjectContextInfo,
    ProjectContextSearchResult,
    ProjectContextSource,
    ProjectContextTextPage,
    VectorRecord,
    chunk_project_context_text,
    normalize_project_context_text,
    project_context_collection_name,
)

PROJECT_CONTEXT_QUERY_INSTRUCTION = (
    "Given the content of an engineering drawing "
    "from the same project, retrieve the most "
    "relevant fragments of the project's "
    "explanatory note. Prefer fragments about "
    "the same equipment, tags, cables, functions, "
    "installation conditions, technical solutions "
    "and design assumptions."
)


@dataclass(frozen=True, slots=True)
class CreateProjectContext:
    """Создаёт временный Qdrant index для одного analysis document."""

    embedding_provider: EmbeddingProvider
    vector_store: VectorStore

    collection_prefix: str
    embedding_model: str

    chunk_size: int
    chunk_overlap: int

    embed_batch_size: int
    upsert_batch_size: int

    async def execute(
        self,
        *,
        context_id: UUID,
        enabled: bool,
        pages: tuple[
            ProjectContextTextPage,
            ...,
        ],
    ) -> ProjectContextInfo:
        """Создаёт deterministic временную collection."""
        if not enabled:
            return ProjectContextInfo(
                context_id=context_id,
                enabled=False,
                collection_name=None,
                pages_count=0,
                chunks_count=0,
                vector_size=0,
            )

        chunks = self._build_chunks(
            pages,
        )

        if not chunks:
            raise ProjectContextError(
                "В диапазоне ПЗ нет текста для индексации.",
            )

        vectors: list[list[float]] = []

        for start in range(
            0,
            len(
                chunks,
            ),
            self.embed_batch_size,
        ):
            batch = chunks[start : start + self.embed_batch_size]

            batch_vectors = await self.embedding_provider.embed(
                tuple(chunk.text for chunk in batch),
                instruction=None,
            )

            vectors.extend(
                batch_vectors,
            )

        if len(
            vectors,
        ) != len(
            chunks,
        ):
            raise ProjectContextError(
                "Количество Project Context embeddings "
                "не совпадает с количеством chunks.",
            )

        if not vectors or not vectors[0]:
            raise ProjectContextError(
                "Project Context embedding пуст.",
            )

        vector_size = len(
            vectors[0],
        )

        if any(
            len(
                vector,
            )
            != vector_size
            for vector in vectors
        ):
            raise ProjectContextError(
                "Project Context embeddings имеют разную размерность.",
            )

        collection = project_context_collection_name(
            prefix=(self.collection_prefix),
            context_id=context_id,
        )

        records = tuple(
            VectorRecord(
                point_id=str(
                    uuid5(
                        NAMESPACE_URL,
                        (
                            f"pdrd-project-context:"
                            f"{context_id}:"
                            f"{chunk.page_number}:"
                            f"{chunk.chunk_index}"
                        ),
                    )
                ),
                vector=vector,
                payload={
                    "page": (chunk.page_number),
                    "chunk_index": (chunk.chunk_index),
                    "text": (chunk.text),
                },
            )
            for (
                chunk,
                vector,
            ) in zip(
                chunks,
                vectors,
                strict=True,
            )
        )

        created = False

        try:
            if await self.vector_store.collection_exists(
                collection,
            ):
                await self.vector_store.delete_collection(
                    collection=collection,
                )

            await self.vector_store.create_collection(
                collection=collection,
                vector_size=vector_size,
            )

            created = True

            for start in range(
                0,
                len(
                    records,
                ),
                self.upsert_batch_size,
            ):
                await self.vector_store.upsert(
                    collection=collection,
                    records=records[start : start + self.upsert_batch_size],
                )

        except (
            EmbeddingProviderError,
            VectorStoreError,
        ):
            if created:
                with suppress(
                    VectorStoreError,
                ):
                    await self.vector_store.delete_collection(
                        collection=collection,
                    )

            raise

        return ProjectContextInfo(
            context_id=context_id,
            enabled=True,
            collection_name=collection,
            pages_count=len(
                pages,
            ),
            chunks_count=len(
                chunks,
            ),
            vector_size=vector_size,
        )

    def _build_chunks(
        self,
        pages: tuple[
            ProjectContextTextPage,
            ...,
        ],
    ) -> tuple[
        ProjectContextChunk,
        ...,
    ]:
        """Разбивает все страницы ПЗ на chunks."""
        result: list[ProjectContextChunk] = []

        for page in sorted(
            pages,
            key=lambda item: item.page_number,
        ):
            page_chunks = chunk_project_context_text(
                page.text,
                chunk_size=(self.chunk_size),
                overlap=(self.chunk_overlap),
            )

            result.extend(
                ProjectContextChunk(
                    page_number=(page.page_number),
                    chunk_index=index,
                    text=text,
                )
                for (
                    index,
                    text,
                ) in enumerate(
                    page_chunks,
                    start=1,
                )
            )

        return tuple(
            result,
        )


@dataclass(frozen=True, slots=True)
class SearchProjectContext:
    """Ищет релевантные chunks в ПЗ текущего проекта."""

    embedding_provider: EmbeddingProvider
    vector_store: VectorStore

    collection_prefix: str
    embedding_model: str

    top_k: int

    async def execute(
        self,
        *,
        context_id: UUID,
        enabled: bool,
        query: str,
    ) -> ProjectContextSearchResult:
        """Выполняет instruction-aware semantic retrieval."""
        normalized = normalize_project_context_text(
            query,
        )

        if not enabled or not normalized:
            return ProjectContextSearchResult(
                context_id=context_id,
                query=normalized,
                sources=(),
                embedding_model=(self.embedding_model),
            )

        collection = project_context_collection_name(
            prefix=(self.collection_prefix),
            context_id=context_id,
        )

        vectors = await self.embedding_provider.embed(
            (normalized,),
            instruction=(PROJECT_CONTEXT_QUERY_INSTRUCTION),
        )

        if (
            len(
                vectors,
            )
            != 1
            or not vectors[0]
        ):
            raise ProjectContextError(
                "Не удалось построить Project Context query embedding.",
            )

        points = await self.vector_store.search(
            collection=collection,
            vector=vectors[0],
            limit=self.top_k,
        )

        sources = tuple(
            ProjectContextSource(
                source_id=(f"PZ{index}"),
                point_id=(point.point_id),
                score=round(
                    point.score,
                    4,
                ),
                page=self._optional_int(
                    point.payload.get(
                        "page",
                    )
                ),
                chunk_index=(
                    self._optional_int(
                        point.payload.get(
                            "chunk_index",
                        )
                    )
                ),
                text=str(
                    point.payload.get(
                        "text",
                        "",
                    )
                    or ""
                ),
            )
            for (
                index,
                point,
            ) in enumerate(
                points,
                start=1,
            )
        )

        return ProjectContextSearchResult(
            context_id=context_id,
            query=normalized,
            sources=sources,
            embedding_model=(self.embedding_model),
        )

    @staticmethod
    def _optional_int(
        value: object,
    ) -> int | None:
        """Возвращает int metadata либо None."""
        try:
            if value is None:
                return None

            return int(
                value,
            )

        except (
            TypeError,
            ValueError,
        ):
            return None


@dataclass(frozen=True, slots=True)
class DeleteProjectContext:
    """Идемпотентно удаляет Project Context по context_id."""

    vector_store: VectorStore

    collection_prefix: str

    async def execute(
        self,
        *,
        context_id: UUID,
    ) -> bool:
        """Удаляет deterministic временную collection."""
        collection = project_context_collection_name(
            prefix=(self.collection_prefix),
            context_id=context_id,
        )

        return await self.vector_store.delete_collection(
            collection=collection,
        )
