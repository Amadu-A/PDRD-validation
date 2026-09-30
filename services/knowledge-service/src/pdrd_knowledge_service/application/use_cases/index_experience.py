# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/index_experience.py

"""Идемпотентная синхронизация E через владельца каталога и общую embedding-модель.

Ни PostgreSQL Experience, ни его файлы не читаются напрямую. Очистка старых
точек выполняется только после полного успешного обхода, без удаления коллекции.
"""

import math
from dataclasses import dataclass

from pdrd_knowledge_service.application.ports.experience_feed import (
    ExperienceFeed,
    ExperienceFeedError,
)
from pdrd_knowledge_service.application.ports.multimodal_embedding import (
    MultimodalEmbeddingInput,
    MultimodalEmbeddingProvider,
    MultimodalEmbeddingProviderError,
)
from pdrd_knowledge_service.application.ports.vector_store import VectorStore
from pdrd_knowledge_service.core.observability import log_execution_time
from pdrd_knowledge_service.domain.experience_index import KIND, TrustedExample
from pdrd_knowledge_service.domain.project_context import VectorRecord

INDEX_INSTRUCTION = (
    "Represent this engineer-reviewed design finding and its verified image region "
    "for retrieval of similar engineering findings."
)


@dataclass(frozen=True, slots=True)
class SyncExperienceIndex:
    """Индексирует только проверенную редакцию и удаляет утратившие актуальность точки."""

    source: ExperienceFeed
    embeddings: MultimodalEmbeddingProvider
    vectors: VectorStore
    collection: str
    identity: str
    dimension: int
    page_size: int = 50

    @log_execution_time(operation="experience_index_sync")
    async def execute(self) -> dict:
        """Повтор обхода восстанавливает индекс после сбоя сети, embedding или Qdrant."""
        if not await self.vectors.collection_exists(self.collection):
            await self.vectors.create_collection(
                collection=self.collection, vector_size=self.dimension
            )
        stored = {
            point.point_id: point.payload
            for point in await self.vectors.scroll_payloads(collection=self.collection)
        }
        seen, cursors = set(), set()
        after = None
        stats = {"examples": 0, "indexed": 0, "unchanged": 0, "stale": 0, "deleted": 0}
        while True:
            examples, next_after = await self.source.page(
                after=after, limit=self.page_size
            )
            for example in examples:
                stats["examples"] += 1
                variants = example.variants()
                expected = {
                    example.point_id(target=target, crop_index=index)
                    for target, _, index in variants
                }
                if all(
                    stored.get(point_id, {}).get("fingerprint")
                    == example.reference.fingerprint
                    and stored[point_id].get("embedding_identity") == self.identity
                    and stored[point_id].get("kind") == KIND
                    for point_id in expected
                ):
                    seen.update(expected)
                    stats["unchanged"] += len(expected)
                    continue
                records = await self._records(example)
                if not await self.source.verify((example.reference,)):
                    stats["stale"] += 1
                    continue
                await self.vectors.upsert(collection=self.collection, records=records)
                seen.update(expected)
                stats["indexed"] += len(records)
            if next_after is None:
                break
            if next_after in cursors or (after is not None and next_after <= after):
                raise ExperienceFeedError("Источник E повторил либо уменьшил курсор.")
            cursors.add(next_after)
            after = next_after
        for point_id, payload in stored.items():
            if payload.get("kind") == KIND and point_id not in seen:
                await self.vectors.delete_by_filter(
                    collection=self.collection, key="index_point_id", value=point_id
                )
                stats["deleted"] += 1
        return {
            **stats,
            "collection": self.collection,
            "embedding_identity": self.identity,
        }

    async def _records(self, example: TrustedExample) -> tuple[VectorRecord, ...]:
        """Один ограниченный batch на пример: до четырёх crop и двух формулировок."""
        images = [
            await self.source.crop(example=example, index=index)
            for index in range(len(example.data["crops"]))
        ]
        variants = example.variants()
        inputs = tuple(
            MultimodalEmbeddingInput(
                text=f"{text}\nНормативное основание инженера: {example.data['normative_basis']}",
                image_bytes=images[index],
                instruction=INDEX_INSTRUCTION,
            )
            for _, text, index in variants
        )
        vectors = await self.embeddings.embed(inputs)
        if len(vectors) != len(inputs) or any(
            len(vector) != self.dimension
            or not any(vector)
            or any(
                type(value) not in (int, float) or not math.isfinite(value)
                for value in vector
            )
            for vector in vectors
        ):
            raise MultimodalEmbeddingProviderError(
                "Embedding E вернул несовместимые векторы."
            )
        return tuple(
            VectorRecord(
                point_id=example.point_id(target=target, crop_index=index),
                vector=vector,
                payload=example.payload(
                    target=target, crop_index=index, identity=self.identity
                ),
            )
            for (target, _, index), vector in zip(variants, vectors, strict=True)
        )
