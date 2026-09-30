# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/health.py

"""Readiness Knowledge Service."""

import asyncio
from dataclasses import dataclass

from pdrd_knowledge_service.application.ports.embedding import (
    EmbeddingProvider,
)
from pdrd_knowledge_service.application.ports.readiness import (
    DatabaseReadinessPort,
)
from pdrd_knowledge_service.application.ports.vector_store import (
    VectorStore,
)
from pdrd_knowledge_service.domain.search import (
    ReadinessReport,
)


@dataclass(frozen=True, slots=True)
class CheckReadiness:
    """Проверяет PostgreSQL, Ollama и Qdrant."""

    database_probe: DatabaseReadinessPort
    embedding_provider: EmbeddingProvider
    vector_store: VectorStore

    normative_collection: str
    experience_collection: str | None

    async def _experience_ready(self) -> bool:
        """Рабочие версии проверяются при поиске; общий API не требует legacy-коллекции."""
        return (
            self.experience_collection is None
            or await self.vector_store.collection_exists(self.experience_collection)
        )

    async def execute(
        self,
    ) -> ReadinessReport:
        """Возвращает состояние внешних dependencies."""
        (
            database_ready,
            embedding_ready,
            qdrant_ready,
        ) = await asyncio.gather(
            self.database_probe.is_ready(),
            self.embedding_provider.is_ready(),
            self.vector_store.is_ready(),
        )

        if not qdrant_ready:
            return ReadinessReport(
                database=database_ready,
                embedding_model=embedding_ready,
                qdrant=False,
                normative_collection=False,
                experience_collection=False,
            )

        (
            normative_exists,
            experience_exists,
        ) = await asyncio.gather(
            self.vector_store.collection_exists(
                self.normative_collection,
            ),
            self._experience_ready(),
        )

        return ReadinessReport(
            database=database_ready,
            embedding_model=embedding_ready,
            qdrant=True,
            normative_collection=normative_exists,
            experience_collection=experience_exists,
        )
