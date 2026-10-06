# services/knowledge-service/tests/unit/test_section_vector_cleanup.py

"""Контракт фильтрованной идемпотентной очистки Qdrant."""

from uuid import uuid4

import httpx
from pdrd_knowledge_service.infrastructure.vector_store.qdrant import QdrantVectorStore


async def test_missing_collection_is_idempotent_and_only_filtered_points_are_deleted(
    monkeypatch,
) -> None:
    """Отсутствующая коллекция считается уже очищенной, DELETE collection не вызывается."""
    requests = []
    original = httpx.AsyncClient

    def client(**options):
        """Подключает тестовый транспорт, сохраняя фактический формат запроса Qdrant."""
        return original(**options, transport=httpx.MockTransport(handle))

    def handle(request):
        """Проверяет границу filter и имитирует отсутствие целевой коллекции."""
        requests.append(request)
        return httpx.Response(404, json={"status": {"error": "Not found"}})

    monkeypatch.setattr(httpx, "AsyncClient", client)
    adapter = QdrantVectorStore(
        base_url="http://qdrant:6333",
        request_timeout_seconds=2,
        health_timeout_seconds=2,
    )
    section_id = str(uuid4())
    await adapter.delete_by_filter(
        collection="shared", key="section_id", value=section_id
    )
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/collections/shared/points/delete"
    assert request.url.params["wait"] == "true"
    import json

    assert json.loads(request.content) == {
        "filter": {"must": [{"key": "section_id", "match": {"value": section_id}}]}
    }
