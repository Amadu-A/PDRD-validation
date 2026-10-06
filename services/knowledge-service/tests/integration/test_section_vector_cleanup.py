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
