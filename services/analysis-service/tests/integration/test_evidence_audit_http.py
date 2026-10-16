# services/analysis-service/tests/integration/test_evidence_audit_http.py

"""Проверка доказательств через существующие HTTP DTO без новых полей и модели."""

from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from pdrd_analysis_service.application.use_cases.document_context import (
    CheckCrossPageConsistency,
)
from pdrd_analysis_service.application.use_cases.finalization import FinalizeFindings
from pdrd_analysis_service.transport.http.document_context_routes import _options
from pdrd_analysis_service.transport.http.routes import _finding_draft_payload

from tests.functional.test_engineering_evidence_gate import candidate, fact

from .test_engineering_program_http import ForbiddenVision
from .test_http_api import build_app, facts_payload


@pytest.mark.parametrize("enabled", [True, False])
async def test_http_diagnostics_and_specific_question_survive_roundtrip_without_new_generation(
    enabled,
):
    """Ложный типоразмер уходит в диагностику, вопрос о выборе диаметра остаётся с обеими рамками."""
    a, b = fact("Ø110x10"), fact("Ø110*10", 2)
    false = candidate(a, b, finding_id="false-size")
    question = candidate(
        a, claim_type="missing_justification", finding_id="diameter-choice"
    )
    app, model = build_app(), ForbiddenVision()
    app.state.container = replace(
        app.state.container,
        check_cross_page_consistency=CheckCrossPageConsistency(
            model, _options(app.state.container)
        ),
        finalize_findings=FinalizeFindings(model, 2000, 8, 1000, 0.5),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/internal/v1/document-context/check-consistency",
            json={
                "document_id": str(uuid4()),
                "enabled": enabled,
                "pages": [
                    {
                        "page_number": item.page,
                        "extracted_text": item.fact.evidence_text,
                        "page_facts": facts_payload(),
                    }
                    for item in (a, b)
                ],
                "page_checks": [
                    {
                        "page_number": 1,
                        "result": {
                            "summary": "Исходные кандидаты",
                            "metrics": {},
                            "findings": [
                                _finding_draft_payload(item).model_dump(mode="json")
                                for item in (false, question)
                            ],
                        },
                    }
                ],
            },
        )
        assert response.status_code == 200, response.text
        findings = [
            finding
            for item in response.json()["items"]
            for finding in item["result"]["findings"]
        ]
        assert {item["finding_id"] for item in findings} == {
            "false-size",
            "diameter-choice",
        }
        final = await client.post(
            "/internal/v1/findings/finalize",
            json={"findings": findings, "experience_by_finding": {}},
        )
        assert final.status_code == 200, final.text
        body = final.json()
        assert [item["finding_id"] for item in body["findings"]] == ["diameter-choice"]
        assert body["findings"][0]["status"] == "needs_review"
        assert body["metrics"]["vlm_call_count"] == 0
        diagnostic = body["metrics"]["rejected_findings"][0]
        assert diagnostic["finding_id"] == "false-size"
        assert len(diagnostic["candidate"]["origin_assertions"][0]["observations"]) == 2
