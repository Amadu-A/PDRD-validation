# services/knowledge-service/src/pdrd_knowledge_service/core/embedding_runtime.py

"""Собирает единую политику embedding для HTTP и фоновой индексации."""

from sqlalchemy.ext.asyncio import AsyncEngine

from pdrd_knowledge_service.core.settings import Settings
from pdrd_knowledge_service.infrastructure.database.embedding_slots import (
    PostgresEmbeddingSlots,
)
from pdrd_knowledge_service.infrastructure.embedding.request_runtime import (
    EmbeddingRequestPolicy,
    EmbeddingRequestRuntime,
)


def build_embedding_runtime(
    settings: Settings,
    engine: AsyncEngine,
    *,
    stage: str = "embedding",
    document_id: str = "",
) -> EmbeddingRequestRuntime:
    """Использует одну ёмкость допуска для N/T/U/D/PZ/E и не изменяет shared infrastructure."""
    policy = EmbeddingRequestPolicy(**settings.embedding_requests.model_dump())
    slots = PostgresEmbeddingSlots(
        engine, identity=settings.embedding_model, capacity=policy.max_concurrency
    )
    return EmbeddingRequestRuntime(
        policy, shared_slot=slots.try_slot, stage=stage, document_id=document_id
    )
