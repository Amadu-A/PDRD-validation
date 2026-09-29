# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/trusted_experience.py

"""Поиск E с проверкой текущей редакции непосредственно перед выдачей контекста.

Qdrant ранжирует похожесть, но не назначает теги, решения, текст и доверие.
При недоступности владельца каталога старый векторный payload не используется.
"""

import asyncio
import math
from dataclasses import dataclass
from uuid import UUID

from pdrd_knowledge_service.application.ports.embedding import (
    EmbeddingProvider,
    EmbeddingProviderError,
)
from pdrd_knowledge_service.application.ports.experience_feed import ExperienceFeed
from pdrd_knowledge_service.application.ports.vector_store import VectorStore
from pdrd_knowledge_service.application.use_cases.experience import (
    EXPERIENCE_QUERY_INSTRUCTION,
)
from pdrd_knowledge_service.core.observability import log_execution_time
from pdrd_knowledge_service.domain.experience_index import KIND, ExampleReference
from pdrd_knowledge_service.domain.search import (
    ExperienceSearchResult,
    ExperienceSource,
    VectorSearchCondition,
    VectorSearchFilter,
)


@dataclass(frozen=True, slots=True)
class SearchTrustedExperience:
    """Применяет порог похожести, исключает повторы crop и проверяет серверные версии."""

    embedding_provider: EmbeddingProvider
    vector_store: VectorStore
    source: ExperienceFeed
    collection: str
    embedding_model: str
    identity: str
    top_k: int = 3
    min_score: float = 0.65
    enabled: bool = False

    @log_execution_time(operation="trusted_experience_search")
    async def execute(self, queries: list[str]) -> tuple[ExperienceSearchResult, ...]:
        """Выключенный режим не делает сетевых запросов и не нагружает shared GPU."""
        normalized = tuple(query.strip() for query in queries)
        if any(not query for query in normalized):
            raise ValueError("Запрос E не может быть пустым.")
        if not self.enabled or not normalized:
            return tuple(
                ExperienceSearchResult(query, (), self.embedding_model)
                for query in normalized
            )
        unique = tuple(dict.fromkeys(normalized))
        vectors = await self.embedding_provider.embed(
            unique, instruction=EXPERIENCE_QUERY_INSTRUCTION
        )
        if len(vectors) != len(unique):
            raise EmbeddingProviderError("Неверное число векторов поиска E.")
        scope = VectorSearchFilter(
            must=(
                VectorSearchCondition("kind", (KIND,)),
                VectorSearchCondition("embedding_identity", (self.identity,)),
            )
        )
        groups = await asyncio.gather(
            *(
                self.vector_store.search_filtered(
                    collection=self.collection,
                    vector=vector,
                    limit=min(100, self.top_k * 4),
                    search_filter=scope,
                )
                for vector in vectors
            )
        )
        found = {}
        for query, points in zip(unique, groups, strict=True):
            candidates = []
            for point in points:
                payload = point.payload
                try:
                    if (
                        payload["kind"] != KIND
                        or payload["embedding_identity"] != self.identity
                        or not math.isfinite(point.score)
                        or point.score < self.min_score
                    ):
                        continue
                    reference = ExampleReference(
                        UUID(payload["example_id"]),
                        payload["example_revision"],
                        payload["fingerprint"],
                    )
                    if (
                        type(reference.example_revision) is not int
                        or reference.example_revision < 0
                        or not isinstance(reference.fingerprint, str)
                        or len(reference.fingerprint) != 64
                    ):
                        continue
                    candidates.append((point, reference))
                except (KeyError, TypeError, ValueError):
                    continue
            current = {
                item.reference: item
                for item in await self.source.verify(
                    tuple(dict.fromkeys(reference for _, reference in candidates))
                )
            }
            selected, used = [], set()
            for point, reference in candidates:
                example = current.get(reference)
                if example is None or reference.example_id in used:
                    continue
                target, index = (
                    point.payload.get("target"),
                    point.payload.get("crop_index"),
                )
                variants = example.variants()
                if type(index) is not int or not any(
                    target == offered and index == area for offered, _, area in variants
                ):
                    continue
                if point.point_id != example.point_id(target=target, crop_index=index):
                    continue
                data = example.data
                negative = data["learning_use"] == "negative"
                text = next(
                    text
                    for offered, text, area in variants
                    if offered == target and area == index
                )
                context = (
                    (
                        "Отрицательный пример: инженер отклонил эту формулировку. "
                        "Не повторяйте её как установленную ошибку. "
                        if negative
                        else "Положительный пример: инженер принял замечание. Это не подтверждает исправление проекта. "
                    )
                    + f"Тег: {data['tag']}; объект разметки: {target}. {text}\n"
                    + f"Причина отказа: {data['rejection_reason']}\n"
                    + f"Основание инженера (не источник N): {data['normative_basis']}"
                )
                selected.append(
                    ExperienceSource(
                        source_id=f"E{len(selected) + 1}",
                        point_id=point.point_id,
                        score=round(point.score, 4),
                        project_id=data["document_id"],
                        issue_id=data["finding_id"],
                        issue_text=text,
                        status=data["learning_use"],
                        verified_fixed=False,
                        before_page=data["page_number"],
                        after_page=None,
                        before_context=context,
                        after_context="",
                        example_id=str(reference.example_id),
                        example_revision=reference.example_revision,
                        tag=data["tag"],
                        decision=data["decision"],
                        learning_use=data["learning_use"],
                        negative_target=target if negative else None,
                    )
                )
                used.add(reference.example_id)
                if len(selected) == self.top_k:
                    break
            found[query] = ExperienceSearchResult(
                query, tuple(selected), self.embedding_model
            )
        return tuple(found[query] for query in normalized)
