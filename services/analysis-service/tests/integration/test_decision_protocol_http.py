# services/analysis-service/tests/integration/test_decision_protocol_http.py

"""Сквозное сохранение программного реестра в действующем HTTP-контракте анализа."""

import logging
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from pdrd_analysis_service.application.use_cases.document_context import (
    CheckCrossPageConsistency,
)
from pdrd_analysis_service.transport.http.document_context_routes import _options
from pdrd_analysis_service.transport.http.schemas import FindingDraftPayload

from .test_document_context_http import ConfirmingVision, fact_payload
from .test_http_api import build_app, facts_payload


class CountingVision(ConfirmingVision):
    """Проверяет бюджет существующего cross-page вызова без реального провайдера."""

    def __init__(self):
        """Сохраняет только параметры тестового запроса."""
        self.calls = []

    async def generate_json(self, **kwargs):
        """Передаёт ответ штатному фейку сопоставимости."""
        self.calls.append(kwargs)
        return await super().generate_json(**kwargs)


@pytest.mark.parametrize("enabled", [True, False])
async def test_existing_http_keeps_protocol_sources_and_geometry_without_extra_calls(
    enabled, caplog
):
    """Протокол переживает JSON; выключенный D не строит скрытых межстраничных связей."""
    app, model = build_app(), CountingVision()
    app.state.container = replace(
        app.state.container,
        check_cross_page_consistency=CheckCrossPageConsistency(
            model, _options(app.state.container)
        ),
    )
    document_id = str(uuid4())
    region = {"x_min": 10, "y_min": 20, "x_max": 50, "y_max": 60, "confidence": 0.9}
    pages = []
    for number, value in [(3, "-37"), (7, "-35")]:
        raw = {**fact_payload(value), "visual_regions": [region]}
        pages.append(
            {
                "page_number": number,
                "extracted_text": raw["evidence_text"],
                "page_facts": {**facts_payload(), "document_facts": [raw]},
            }
        )
    caplog.set_level(logging.INFO)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/internal/v1/document-context/check-consistency",
            json={"document_id": document_id, "pages": pages, "enabled": enabled},
        )
    assert response.status_code == 200, response.text
    if not enabled:
        assert model.calls == [] and response.json()["findings"] == []
        assert "design_decision_registry" not in caplog.text
        return
    assert len(model.calls) == 1
    assert model.calls[0]["stage"] == "cross_page_consistency"
    (finding,) = response.json()["findings"]
    origins = finding["origin_assertions"]
    assert {item["origin"] for item in origins} == {
        "cross_page_validation",
        "engineering_assertion",
        "design_decision_registry",
    }
    protocol = next(
        item for item in origins if item["origin"] == "design_decision_registry"
    )
    assert protocol["document_id"] == document_id
    (decision,) = protocol["decisions"]
    assert decision["evidence_ids"] == ["p0003-f0001", "p0007-f0001"]
    assert [item["observation"]["page"] for item in decision["evidence"]] == [3, 7]
    assert all(
        item["observation"]["fact"]["visual_regions"] == [region]
        for item in decision["evidence"]
    )
    decoded = FindingDraftPayload.model_validate(finding).to_domain()
    assert len(decoded.document_context_basis_sources) == 2
    assert [item["page"] for item in decoded.evidence_locations] == [3, 7]
    # Эксплуатационная запись содержит счётчики, исходный текст в неё не попадает.
    log = next(
        item.message
        for item in caplog.records
        if item.message.startswith("design_decision_registry ")
    )
    assert document_id in log and "new_vlm_calls=0" in log
    assert "Б-012" not in log and "-37" not in log
