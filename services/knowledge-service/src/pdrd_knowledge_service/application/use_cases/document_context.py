# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/document_context.py

"""Временный индекс проверяемого PDF, изолированный по заданию."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from time import time_ns
from uuid import NAMESPACE_URL, UUID, uuid5

from pdrd_knowledge_service.application.ports.embedding import EmbeddingProvider
from pdrd_knowledge_service.application.ports.vector_store import VectorStore
from pdrd_knowledge_service.domain.project_context import (
    ProjectContextInfo,
    ProjectContextSearchResult,
    ProjectContextSource,
    VectorRecord,
    chunk_project_context_text,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DocumentContextPage:
    """Физическая страница и извлечённые атомарные факты."""

    page_number: int
    text: str
    facts: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class DocumentContextIndex:
    """Общие операции над временными коллекциями без повторного кэша ПЗ."""

    vector_store: VectorStore
    collection_prefix: str

    @property
    def prefix(self) -> str:
        """Отделяет новые временные коллекции от прежних кэшей."""
        return f"{self.collection_prefix}_job_"

    async def collections(self, document_id: UUID) -> tuple[str, ...]:
        """Ищет только физические коллекции одного задания."""
        prefix = f"{self.prefix}{document_id.hex}_"
        return tuple(
            name
            for name in await self.vector_store.list_collections()
            if name.startswith(prefix)
        )

    async def cleanup(self, *, context_id: UUID) -> None:
        """Повторное удаление уже очищенного задания безопасно."""
        for name in await self.collections(context_id):
            await self.vector_store.delete_collection(collection=name)

    async def stale(
        self, *, before: datetime, limit: int, cursor: str = ""
    ) -> tuple[tuple[UUID, ...], str]:
        """Просматривает ограниченный пакет имён; время есть даже у пустой коллекции."""
        names = sorted(
            name
            for name in await self.vector_store.list_collections()
            if name.startswith(self.prefix) and name > cursor
        )
        selected = names[:limit]
        candidates = []
        for name in selected:
            identity, _, created = name[len(self.prefix) :].partition("_")
            try:
                document_id = UUID(hex=identity)
                created_at = datetime.fromtimestamp(int(created) / 1_000_000_000, UTC)
            except (ValueError, OverflowError, OSError):
                continue
            if created_at < before and document_id not in candidates:
                candidates.append(document_id)
        return tuple(candidates), selected[-1] if len(names) > limit else ""


@dataclass(frozen=True, slots=True)
class BuildDocumentContext:
    """Пересоздаёт индекс этого задания; одинаковый PDF не разделяет индекс."""

    index: DocumentContextIndex
    embedding_provider: EmbeddingProvider
    chunk_size: int
    chunk_overlap: int
    embed_batch_size: int
    upsert_batch_size: int

    async def execute(
        self,
        *,
        document_id: UUID,
        source_sha256: str,
        pages: tuple[DocumentContextPage, ...],
    ) -> ProjectContextInfo:
        """Индексирует текст и факты с неизменными физическими ID фрагментов."""
        if len(source_sha256) != 64 or any(
            c not in "0123456789abcdef" for c in source_sha256.lower()
        ):
            raise ValueError("Неверный SHA256 исходного PDF.")
        if (
            not pages
            or len({page.page_number for page in pages}) != len(pages)
            or any(page.page_number < 1 for page in pages)
        ):
            raise ValueError("Страницы D-контекста пусты либо повторяются.")
        chunks = tuple(
            (page.page_number, index, text)
            for page in sorted(pages, key=lambda item: item.page_number)
            for index, text in enumerate(
                chunk_project_context_text(
                    page.text
                    + "\n\nФАКТЫ СТРАНИЦЫ:\n"
                    + "\n".join(
                        "; ".join(
                            str(value)
                            for key, value in fact.items()
                            if key != "visual_regions"
                        )
                        for fact in page.facts
                    ),
                    chunk_size=self.chunk_size,
                    overlap=self.chunk_overlap,
                )
            )
        )
        await self.index.cleanup(context_id=document_id)
        name = f"{self.index.prefix}{document_id.hex}_{time_ns()}"
        vector_size = 0
        created = False
        try:
            for start in range(0, len(chunks), self.embed_batch_size):
                subset = chunks[start : start + self.embed_batch_size]
                vectors = await self.embedding_provider.embed(
                    tuple(item[2] for item in subset), instruction=None
                )
                if len(vectors) != len(subset) or not vectors or not vectors[0]:
                    raise ValueError("Провайдер вернул неполный набор векторов D.")
                if not created:
                    vector_size = len(vectors[0])
                    await self.index.vector_store.create_collection(
                        collection=name, vector_size=vector_size
                    )
                    created = True
                records = tuple(
                    VectorRecord(
                        point_id=str(
                            uuid5(NAMESPACE_URL, f"{document_id}:{page}:{chunk}")
                        ),
                        vector=vector,
                        payload={
                            "document_id": str(document_id),
                            "source_sha256": source_sha256,
                            "page": page,
                            "chunk_index": chunk,
                            "text": text,
                            "source_kind": "D",
                        },
                    )
                    for (page, chunk, text), vector in zip(subset, vectors, strict=True)
                )
                for offset in range(0, len(records), self.upsert_batch_size):
                    await self.index.vector_store.upsert(
                        collection=name,
                        records=records[offset : offset + self.upsert_batch_size],
                    )
        except BaseException:
            try:
                await self.index.vector_store.delete_collection(collection=name)
            except Exception:
                logger.exception(
                    "document_context_build_cleanup_failed document_id=%s", document_id
                )
            raise
        return ProjectContextInfo(
            context_id=document_id,
            enabled=True,
            collection_name=name if created else None,
            pages_count=len(pages),
            chunks_count=len(chunks),
            vector_size=vector_size,
            cache_hit=False,
        )


@dataclass(frozen=True, slots=True)
class SearchDocumentContext:
    """Семантический поиск с ID страницы и чанка, независимым от ранга ответа."""

    index: DocumentContextIndex
    embedding_provider: EmbeddingProvider
    embedding_model: str
    top_k: int

    async def execute(
        self, *, context_id: UUID, enabled: bool, query: str
    ) -> ProjectContextSearchResult:
        """Ищет только во временном индексе текущего задания."""
        names = await self.index.collections(context_id) if enabled else ()
        sources = ()
        if names and query.strip():
            vectors = await self.embedding_provider.embed(
                (query,),
                instruction=(
                    "Найди в этом же PDF связанные объекты, параметры и продолжения таблиц с теми же условиями."
                ),
            )
            points = await self.index.vector_store.search(
                collection=max(names), vector=vectors[0], limit=self.top_k
            )
            sources = tuple(
                ProjectContextSource(
                    source_id=f"D-p{int(point.payload['page']):04d}-c{int(point.payload['chunk_index'])}",
                    point_id=point.point_id,
                    score=point.score,
                    page=int(point.payload["page"]),
                    chunk_index=int(point.payload["chunk_index"]),
                    text=str(point.payload["text"]),
                )
                for point in points
            )
        return ProjectContextSearchResult(
            context_id=context_id,
            query=query,
            sources=sources,
            embedding_model=self.embedding_model,
        )
