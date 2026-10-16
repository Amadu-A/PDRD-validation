# services/analysis-service/tests/integration/test_engineering_program_http.py

"""Действующие HTTP-этапы сохраняют программный вывод, страницы и рамки без новых VLM."""

from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from pdrd_analysis_service.application.use_cases.document_context import (
    CheckCrossPageConsistency,
)
from pdrd_analysis_service.application.use_cases.finalization import FinalizeFindings
from pdrd_analysis_service.application.use_cases.finding_localization import (
    LocalizeFindings,
)
from pdrd_analysis_service.transport.http.document_context_routes import _options

from .test_http_api import build_app, encoded_image, facts_payload


class ForbiddenVision:
    """Ни новый расчёт, ни ссылка, ни отсутствие bbox не должны обращаться к модели."""

    async def generate_json(self, **request):
        """Проверяет нулевой бюджет на действующей HTTP-границе."""
        raise AssertionError("Запрещён дополнительный VLM-вызов")


@pytest.mark.parametrize("enabled", [True, False])
async def test_full_http_program_evaluation_has_no_new_calls_or_contract_fields(
    enabled,
):
    """Числовое равенство из исходного текста доходит до final finding с теми же областями."""
    app, model = build_app(), ForbiddenVision()
    app.state.container = replace(
        app.state.container,
        check_cross_page_consistency=CheckCrossPageConsistency(
            model, _options(app.state.container)
        ),
        finalize_findings=FinalizeFindings(model, 2000, 8, 1000, 0.5),
        localize_findings=LocalizeFindings(model, 1000),
    )
    body = {
        "document_id": str(uuid4()),
        "enabled": enabled,
        "pages": [
            {
                "page_number": 1,
                "extracted_text": "2+3=6 м³/ч",
                "page_facts": {**facts_payload(), "document_facts": []},
                "text_words": [
                    {
                        "text": "2+3=6 м³/ч",
                        "bbox": {"x_min": 10, "y_min": 20, "x_max": 100, "y_max": 50},
                    }
                ],
            }
        ],
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        cross = await client.post(
            "/internal/v1/document-context/check-consistency", json=body
        )
        assert cross.status_code == 200, cross.text
        if not enabled:
            assert cross.json()["findings"] == []
            return
        findings = cross.json()["findings"]
        assert len(findings) == 1 and findings[0]["status"] == "confirmed"
        assert findings[0]["visual_regions"][0]["x_min"] == 10
        assert findings[0]["evidence_locations"][0]["page"] == 1
        response = await client.post(
            "/internal/v1/findings/finalize",
            json={"findings": findings, "experience_by_finding": {}},
        )
        assert response.status_code == 200, response.text
        assert response.json()["metrics"]["vlm_call_count"] == 0
        final = response.json()["findings"][0]
        assert final["visual_regions"] == findings[0]["visual_regions"]
        assert (
            final["document_context_basis_sources"]
            == findings[0]["document_context_basis_sources"]
        )
        localized = await client.post(
            "/internal/v1/findings/localize",
            json={
                "page_number": 1,
                "extracted_text": "2+3=6 м³/ч",
                "image_base64": encoded_image(),
                "findings": [
                    {key: final[key] for key in ("finding_id", "comment", "evidence")}
                ],
            },
        )
        assert localized.status_code == 200, localized.text
        assert localized.json()["metrics"]["attempt"] == 0
