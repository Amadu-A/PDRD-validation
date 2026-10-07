# services/knowledge-service/tests/integration/test_section_vector_cleanup.py

"""Проверяет физическую очистку точек только в отдельном временном Qdrant."""

import asyncio
import os
from uuid import uuid4

import pytest
from pdrd_knowledge_service.domain.project_context import VectorRecord
from pdrd_knowledge_service.infrastructure.vector_store.qdrant import QdrantVectorStore

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("PDRD_RUN_KNOWLEDGE_VECTOR_TESTS") != "1",
        reason="Требуется отдельный Qdrant runner Knowledge Service",
    ),
]


async def test_section_cleanup_preserves_shared_collection_and_other_sections():
    """Повтор удаления очищает обе области раздела и оставляет векторы соседнего UUID."""
    url = os.environ["KNOWLEDGE_TEST_QDRANT_URL"]
    assert url == "http://knowledge-test-qdrant:6333", (
        "Разрешён только изолированный тестовый Qdrant"
    )
    adapter = QdrantVectorStore(
        base_url=url, request_timeout_seconds=5, health_timeout_seconds=1
    )
    for _ in range(45):
        if await adapter.is_ready():
            break
        await asyncio.sleep(1)
    else:
        pytest.fail("Тестовый Qdrant не стал доступен за 45 секунд")
    collection = "test_section_cleanup_" + uuid4().hex
    target, other = str(uuid4()), str(uuid4())
    records = tuple(
        VectorRecord(
            str(uuid4()),
            [1.0, 0.0],
            {
                "section_id": section,
                "document_id": str(uuid4()),
                "catalog_area": area,
            },
        )
        for section in (target, other)
        for area in ("normative", "user_package")
    )
    try:
        await adapter.create_collection(collection=collection, vector_size=2)
        await adapter.upsert(collection=collection, records=records)
        assert len(await adapter.scroll_payloads(collection=collection)) == 4
        for _ in range(2):
            await adapter.delete_by_filter(
                collection=collection, key="section_id", value=target
            )
        remaining = await adapter.scroll_payloads(collection=collection)
        assert len(remaining) == 2
        assert {point.payload["section_id"] for point in remaining} == {other}
        assert await adapter.collection_exists(collection)
        await adapter.delete_by_filter(
            collection="missing_" + uuid4().hex, key="section_id", value=target
        )
    finally:
        await adapter.delete_collection(collection=collection)


async def test_document_context_job_isolation_stable_sources_and_cleanup_in_qdrant():
    """Одинаковый PDF имеет два независимых временных индекса и стабильные ID чанков."""
    from pdrd_knowledge_service.application.use_cases.document_context import (
        BuildDocumentContext,
        DocumentContextIndex,
        DocumentContextPage,
        SearchDocumentContext,
    )

    url = os.environ["KNOWLEDGE_TEST_QDRANT_URL"]
    assert url == "http://knowledge-test-qdrant:6333"
    store = QdrantVectorStore(
        base_url=url, request_timeout_seconds=5, health_timeout_seconds=1
    )

    class Embeddings:
        """Детерминированные векторы для проверки физического хранения."""

        async def embed(self, texts, *, instruction):
            """Не требует общего GPU в интеграционном runner."""
            return [[1.0, 0.0] for _ in texts]

    index = DocumentContextIndex(store, "test_document_context_" + uuid4().hex)
    builder = BuildDocumentContext(index, Embeddings(), 200, 20, 2, 2)
    search = SearchDocumentContext(index, Embeddings(), "test", 5)
    first, second = uuid4(), uuid4()
    pages = (
        DocumentContextPage(7, "Б-012 -37", ()),
        DocumentContextPage(10, "Б-012 -35", ()),
    )
    try:
        await builder.execute(document_id=first, source_sha256="a" * 64, pages=pages)
        await builder.execute(document_id=second, source_sha256="a" * 64, pages=pages)
        before = await search.execute(context_id=first, enabled=True, query="Б-012")
        assert {source.source_id for source in before.sources} == {
            "D-p0007-c0",
            "D-p0010-c0",
        }
        await index.cleanup(context_id=first)
        await index.cleanup(context_id=first)
        assert not await index.collections(first)
        assert (
            len(
                (
                    await search.execute(context_id=second, enabled=True, query="Б-012")
                ).sources
            )
            == 2
        )
        await builder.execute(document_id=first, source_sha256="a" * 64, pages=pages)
        after = await search.execute(context_id=first, enabled=True, query="Б-012")
        assert {source.source_id for source in after.sources} == {
            source.source_id for source in before.sources
        }
    finally:
        await index.cleanup(context_id=first)
        await index.cleanup(context_id=second)
