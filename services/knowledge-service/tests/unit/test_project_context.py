# services/knowledge-service/tests/unit/test_project_context.py

"""Unit tests temporary Knowledge Project Context."""

from uuid import uuid4

import pytest
from pdrd_knowledge_service.application.use_cases.project_context import (
    CreateProjectContext,
    DeleteProjectContext,
    SearchProjectContext,
)
from pdrd_knowledge_service.domain.project_context import (
    ProjectContextTextPage,
    VectorRecord,
)
from pdrd_knowledge_service.domain.search import VectorPoint


class FakeEmbeddingProvider:
    """Fake embedding provider."""

    def __init__(
        self,
    ) -> None:
        """Создаёт call history."""
        self.instructions: list[str | None] = []

    async def embed(
        self,
        texts: tuple[str, ...],
        *,
        instruction: str | None,
    ) -> list[list[float]]:
        """Возвращает deterministic vectors."""
        self.instructions.append(
            instruction,
        )

        return [
            [
                float(
                    index + 1,
                ),
                0.5,
                0.25,
            ]
            for index, _ in enumerate(
                texts,
            )
        ]

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает readiness."""
        return True


class FakeVectorStore:
    """In-memory vector store."""

    def __init__(
        self,
    ) -> None:
        """Создаёт test state."""
        self.collections: dict[
            str,
            list[VectorRecord],
        ] = {}

    async def search(
        self,
        *,
        collection: str,
        vector: list[float],
        limit: int,
    ) -> list[VectorPoint]:
        """Возвращает первый сохранённый chunk."""
        assert vector

        records = self.collections[collection]

        return [
            VectorPoint(
                point_id=(records[0].point_id),
                score=0.87,
                payload=(records[0].payload),
            )
        ][:limit]

    async def create_collection(
        self,
        *,
        collection: str,
        vector_size: int,
    ) -> None:
        """Создаёт collection."""
        assert vector_size == 3

        self.collections[collection] = []

    async def upsert(
        self,
        *,
        collection: str,
        records: tuple[
            VectorRecord,
            ...,
        ],
    ) -> None:
        """Сохраняет records."""
        self.collections[collection].extend(
            records,
        )

    async def delete_collection(
        self,
        *,
        collection: str,
    ) -> bool:
        """Идемпотентно удаляет collection."""
        existed = collection in self.collections

        self.collections.pop(
            collection,
            None,
        )

        return existed

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает readiness."""
        return True

    async def collection_exists(
        self,
        collection: str,
    ) -> bool:
        """Проверяет collection."""
        return collection in self.collections


@pytest.mark.asyncio
async def test_create_search_delete_project_context() -> None:
    """Проверяет полный lifecycle temporary context."""
    context_id = uuid4()

    embeddings = FakeEmbeddingProvider()

    vector_store = FakeVectorStore()

    create = CreateProjectContext(
        embedding_provider=embeddings,
        vector_store=vector_store,
        collection_prefix=("pdrd_project_context"),
        embedding_model="test-embedding",
        chunk_size=120,
        chunk_overlap=20,
        embed_batch_size=12,
        upsert_batch_size=64,
    )

    info = await create.execute(
        context_id=context_id,
        enabled=True,
        pages=(
            ProjectContextTextPage(
                page_number=2,
                text=("Описание проектного решения. " * 20),
            ),
        ),
    )

    assert info.enabled is True

    assert info.collection_name == (f"pdrd_project_context_{context_id.hex}")

    assert info.chunks_count > 0

    assert embeddings.instructions[0] is None

    search = SearchProjectContext(
        embedding_provider=embeddings,
        vector_store=vector_store,
        collection_prefix=("pdrd_project_context"),
        embedding_model="test-embedding",
        top_k=5,
    )

    result = await search.execute(
        context_id=context_id,
        enabled=True,
        query="Оборудование проекта",
    )

    assert (
        len(
            result.sources,
        )
        == 1
    )

    assert result.sources[0].source_id == "PZ1"

    assert embeddings.instructions[-1] is not None

    delete = DeleteProjectContext(
        vector_store=vector_store,
        collection_prefix=("pdrd_project_context"),
    )

    assert (
        await delete.execute(
            context_id=context_id,
        )
        is True
    )

    assert (
        await delete.execute(
            context_id=context_id,
        )
        is False
    )
