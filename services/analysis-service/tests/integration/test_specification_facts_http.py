# services/analysis-service/tests/integration/test_specification_facts_http.py

"""Публичный контракт внутреннего HTTP stage сохраняет весь программный D без EQ и GPU."""

import base64
from uuid import uuid4

import httpx
import pytest

from ..unit.test_specification_page_facts import routed_specification
from .test_normative_budget_http import CancellationProbe, application


@pytest.mark.parametrize("d,eq", [(True, False), (True, True), (False, True)])
async def test_http_table_route_preserves_all_provenance_without_model_request(
    monkeypatch, d, eq
):
    """Настоящие DTO и сценарий сохраняют 65 позиций; попытка обращения к GPU запрещена."""
    real_client = httpx.AsyncClient
    async with real_client(
        transport=httpx.ASGITransport(app=application(CancellationProbe(False))),
        base_url="http://test",
    ) as client:

        def forbidden_client(**kwargs):
            """Любой непредусмотренный повтор чтения таблицы делает тест красным."""
            pytest.fail("Текстовая таблица повторно передана в VLM")

        monkeypatch.setattr(httpx, "AsyncClient", forbidden_client)
        response = await client.post(
            "/internal/v1/stages/understand-pages",
            json={
                "document_id": str(uuid4()),
                "items": [
                    {
                        "page_number": 25,
                        "extracted_text": "Спецификация",
                        "heuristic_page_type": "table",
                        "image_base64": base64.b64encode(b"image").decode(),
                        "equipment_specification": routed_specification(),
                        "use_document_context": d,
                        "use_equipment_web_search": eq,
                    }
                ],
            },
        )
    assert response.status_code == 200
    result = response.json()["items"][0]["result"]
    facts = result["facts"]
    assert len(facts["equipment_specification"]["items"]) == 65
    assert (
        len(facts["equipment_identities"]) == 65
        if eq
        else facts["equipment_identities"] is None
    )
    assert len(facts["document_facts"]) == (260 if d else 0)
    for fact in facts["document_facts"]:
        assert fact["source_item_id"] == fact["identifier"] == fact["scope_segment"]
        assert fact["source_row_ids"] and fact["source_pages"] == [25]
        assert (
            fact["source_regions"]
            and fact["visual_regions"]
            and fact["extraction_method"] == "pdf_table"
        )
    assert result["metrics"]["understanding"]["route"] == "pdf_table"
    assert result["metrics"]["understanding"]["vlm_call_count"] == 0
