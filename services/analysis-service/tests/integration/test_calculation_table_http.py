# services/analysis-service/tests/integration/test_calculation_table_http.py

"""Действующий HTTP-конвейер сохраняет вопросы и гипотезы таблицы при D=true и D=false."""

import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pdrd_analysis_service.application.use_cases.document_context import (
    CheckCrossPageConsistency,
)
from pdrd_analysis_service.application.use_cases.finalization import FinalizeFindings
from pdrd_analysis_service.domain.analysis import FindingDraft
from pdrd_analysis_service.transport.http.document_context_routes import _options
from pdrd_analysis_service.transport.http.routes import _finding_draft_payload

from .test_engineering_program_http import ForbiddenVision
from .test_http_api import build_app, facts_payload


@pytest.mark.parametrize("enabled", [True, False])
async def test_existing_http_table_audit_and_finalization_preserve_candidates_without_generation(
    enabled,
):
    """Те же страницы, шесть исходных кандидатов и ноль вызовов на контроль арифметики."""
    raw = json.loads(
        (
            Path(__file__).parents[4]
            / "tests/fixtures/engineering/ios2-calculation-page30.json"
        ).read_text(encoding="utf-8")
    )
    app, model = build_app(), ForbiddenVision()
    app.state.container = replace(
        app.state.container,
        check_cross_page_consistency=CheckCrossPageConsistency(
            model, _options(app.state.container)
        ),
        finalize_findings=FinalizeFindings(model, 2000, 8, 1000, 0.5),
    )
    candidates = []
    for index, (value, unit) in enumerate(
        (
            ("155,122", "м³/сут"),
            ("42,323", "м³/ч"),
            ("12,264", "л/с"),
            ("116,033", "м³/сут"),
            ("4,835", "м³/ч"),
            ("1,343", "л/с"),
        )
    ):
        finding = FindingDraft(
            f"p30-f{index}",
            30,
            "table",
            "document_consistency",
            "warning",
            "confirmed",
            "Ошибка расчёта",
            f"{value} {unit}",
            "Проверить",
            0.9,
            (),
            "",
            (),
            "",
            origin_assertions=({"origin": "vlm", "claim_type": "arithmetic"},),
        )
        candidates.append(_finding_draft_payload(finding).model_dump(mode="json"))
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
                        "page_number": 30,
                        "extracted_text": raw["text"],
                        "text_words": raw["text_words"],
                        "page_facts": facts_payload(),
                    }
                ],
                "page_checks": [
                    {
                        "page_number": 30,
                        "result": {
                            "summary": "Исходные кандидаты",
                            "metrics": {},
                            "findings": candidates,
                        },
                    }
                ],
            },
        )
        assert response.status_code == 200, response.text
        findings = response.json()["items"][0]["result"]["findings"]
        assert len(findings) == len(candidates)
        assert [item["status"] for item in findings] == ["hypothesis"] * 3 + [
            "needs_review"
        ] * 3
        assert all(item["page"] == 30 for item in findings)
        final = await client.post(
            "/internal/v1/findings/finalize",
            json={"findings": findings, "experience_by_finding": {}},
        )
        assert final.status_code == 200, final.text
        assert final.json()["metrics"]["vlm_call_count"] == 0
        assert [item["status"] for item in final.json()["findings"]] == [
            item["status"] for item in findings
        ]
        assert all(
            not item["document_context_basis_sources"]
            for item in final.json()["findings"]
        )
