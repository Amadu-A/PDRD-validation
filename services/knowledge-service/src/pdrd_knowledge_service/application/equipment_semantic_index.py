# services/knowledge-service/src/pdrd_knowledge_service/application/equipment_semantic_index.py

"""Локальный векторный индекс ограниченных фрагментов EQ snapshot."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pdrd_knowledge_service.application.ports.embedding import EmbeddingProvider
from pdrd_knowledge_service.application.ports.vector_store import VectorStore
from pdrd_knowledge_service.domain.project_context import (
    VectorRecord,
    chunk_project_context_text,
)


@dataclass(slots=True)
class EquipmentSemanticIndex:
    """Индексирует документацию в Qdrant только при новом snapshot."""

    embedding_provider: EmbeddingProvider
    vector_store: VectorStore
    max_chunks: int = 128
    embed_batch_size: int = 16
    top_k: int = 8
    _admission: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(1), init=False
    )

    @staticmethod
    def collection_name(source_id: str, digest: str, version: int) -> str:
        """Строит изолированное имя без произвольного ввода в URL Qdrant."""
        key = sha256(f"{source_id}|{digest}|{version}".encode()).hexdigest()[:32]
        return f"pdrd_eq_{key}"

    async def rank_pages(
        self,
        *,
        source_id: str,
        document_sha256: str,
        extractor_version: int,
        model: str,
        properties: tuple[str, ...],
        pages: list[dict[str, Any]],
        should_stop: Callable[[], Awaitable[bool]] | None = None,
    ) -> tuple[int, ...]:
        """Возвращает физические страницы, релевантные модели и свойствам."""
        chunks = tuple(
            (int(page["page"]), index, text)
            for page in pages[:40]
            for index, text in enumerate(
                chunk_project_context_text(
                    str(page.get("text") or "")[:20_000],
                    chunk_size=1200,
                    overlap=100,
                )
            )
        )[: self.max_chunks]
        if not chunks:
            return ()
        collection = self.collection_name(source_id, document_sha256, extractor_version)
        async with self._admission:
            if should_stop is not None and await should_stop():
                raise asyncio.CancelledError
            if not await self.vector_store.collection_exists(collection):
                created = False
                try:
                    for offset in range(0, len(chunks), self.embed_batch_size):
                        if should_stop is not None and await should_stop():
                            raise asyncio.CancelledError
                        batch = chunks[offset : offset + self.embed_batch_size]
                        embeddings = await self.embedding_provider.embed(
                            tuple(text for _, _, text in batch), instruction=None
                        )
                        if len(embeddings) != len(batch) or any(
                            not vector for vector in embeddings
                        ):
                            raise ValueError("Embedding вернул неполный EQ batch.")
                        if not created:
                            await self.vector_store.create_collection(
                                collection=collection,
                                vector_size=len(embeddings[0]),
                            )
                            created = True
                        await self.vector_store.upsert(
                            collection=collection,
                            records=tuple(
                                VectorRecord(
                                    point_id=str(
                                        uuid5(
                                            NAMESPACE_URL,
                                            f"{source_id}:{document_sha256}:{page}:{index}",
                                        )
                                    ),
                                    vector=vector,
                                    payload={
                                        "source_kind": "EQ",
                                        "source_id": source_id,
                                        "document_sha256": document_sha256,
                                        "page": page,
                                        "chunk_index": index,
                                        "text": text,
                                    },
                                )
                                for (page, index, text), vector in zip(
                                    batch, embeddings, strict=True
                                )
                            ),
                        )
                except BaseException:
                    if created:
                        await self.vector_store.delete_collection(collection=collection)
                    raise
            query = " ".join(part for part in (model, *properties[:8]) if part)[:500]
            if not query:
                return ()
            if should_stop is not None and await should_stop():
                raise asyncio.CancelledError
            vectors = await self.embedding_provider.embed(
                (query,),
                instruction=(
                    "Найди технические характеристики указанной модели "
                    "оборудования в документации производителя."
                ),
            )
            if len(vectors) != 1 or not vectors[0]:
                raise ValueError("Embedding не вернул EQ query vector.")
            points = await self.vector_store.search(
                collection=collection,
                vector=vectors[0],
                limit=self.top_k,
            )
        ranked: list[int] = []
        for point in points:
            payload = point.payload
            if (
                payload.get("source_id") != source_id
                or payload.get("document_sha256") != document_sha256
            ):
                continue
            page = int(payload.get("page") or 0)
            if page > 0 and page not in ranked:
                ranked.append(page)
        return tuple(ranked)
