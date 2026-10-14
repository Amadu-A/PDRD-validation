# services/equipment-search-service/tests/unit/test_knowledge_facts.py

"""Проверяет неизменную идентичность snapshot и частичный отказ retrieval."""

import json

import httpx
import pytest
from pdrd_equipment_search_service.domain.equipment import (
    DocumentSnapshot,
    EquipmentIdentity,
)
from pdrd_equipment_search_service.infrastructure.knowledge_facts import (
    KnowledgeEquipmentFactIndex,
)


@pytest.mark.asyncio
async def test_snapshot_identity_is_used_even_when_request_has_alias() -> None:
    """Алиас нового задания не подменяет ключ сохранённого EQ документа."""
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        """Проверяет payload без сети и возвращает частичный результат."""
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "status": "ready",
                "retrieval_status": "unavailable",
                "facts": [{"document_source_id": "EQ-one", "page": 1}],
            },
        )

    snapshot = DocumentSnapshot(
        source_id="EQ-one",
        manufacturer="MEAN WELL",
        model="DRC-100B",
        variant="",
        source_url="https://example.org/doc.pdf",
        final_url="https://example.org/doc.pdf",
        sha256="a" * 64,
        storage_reference="one.pdf",
        revision="R1",
        trust_status="trusted",
    )
    identity = EquipmentIdentity("Meanwell", "drc-100b", properties=("output_voltage",))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        status, facts = await KnowledgeEquipmentFactIndex(
            client, "http://knowledge"
        ).extract_or_get(
            identity,
            snapshot,
            ((1, "DRC-100B Output voltage 24 V DC"),),
        )
    assert requests[0]["manufacturer"] == "MEAN WELL"
    assert requests[0]["model"] == "DRC-100B" and requests[0]["sha256"] == "a" * 64
    assert requests[0]["properties"] == ["output_voltage"]
    assert status == "partial_index" and facts[0]["document_source_id"] == "EQ-one"
