# services/analysis-service/tests/integration/test_evidence_audit_http.py

"""Проверка доказательств через существующие HTTP DTO без новых полей и модели."""

from dataclasses import asdict, replace
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
from tests.functional.test_quality_completeness import source_pages

from .test_engineering_program_http import ForbiddenVision
from .test_http_api import build_app, facts_payload


async def test_real_reference_passes_http_contract_and_finalization_without_generation():
    """Настоящие DTO читают сырой PDF-текст и проверяют ссылки без готового finding во входе."""
    app, model = build_app(), ForbiddenVision()
    app.state.container = replace(
        app.state.container,
        check_cross_page_consistency=CheckCrossPageConsistency(
            model, _options(app.state.container)
        ),
        finalize_findings=FinalizeFindings(model, 4000, 8, 1000, 0.5),
    )
    pages = [page for page in source_pages() if page.page == 20]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/internal/v1/document-context/check-consistency",
            json={
                "document_id": str(uuid4()),
                "enabled": True,
                "pages": [
                    {
                        "page_number": page.page,
                        "extracted_text": page.text,
                        "page_facts": {
                            **facts_payload(),
                            "document_facts": [asdict(f) for f in page.facts],
                        },
                    }
                    for page in pages
                ],
            },
        )
        assert response.status_code == 200, response.text
        findings = response.json()["findings"]
        assert len(findings) == 1 and "таблица 3" in findings[0]["comment"]
        final = await client.post(
            "/internal/v1/findings/finalize",
            json={"findings": findings, "experience_by_finding": {}},
        )
        assert final.status_code == 200, final.text
        assert len(final.json()["findings"]) == 1
        assert final.json()["metrics"]["vlm_call_count"] == 0
        assert (
            final.json()["findings"][0]["document_context_source_ids"]
            == findings[0]["document_context_source_ids"]
        )


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


async def test_http_vector_provenance_is_replayed_without_assuming_cross_page_segment_identity():
    """Настоящие HTTP DTO сохраняют путь выноски; одинаковый тег на двух листах не даёт нарушения."""
    region = {"x_min": 10, "y_min": 10, "x_max": 20, "y_max": 20, "confidence": 0.9}
    observations = (
        fact("15", prop="Длина", unit="м", visual_regions=[region]),
        fact("16", 2, prop="Длина", unit="м", visual_regions=[region]),
    )
    app, model = build_app(), ForbiddenVision()
    app.state.container = replace(
        app.state.container,
        check_cross_page_consistency=CheckCrossPageConsistency(
            model, _options(app.state.container)
        ),
        finalize_findings=FinalizeFindings(model, 2000, 8, 1000, 0.5),
    )
    pages = []
    for item in observations:
        words = [
            {
                "text": "S1",
                "block_no": 1,
                "line_no": 0,
                "word_no": 0,
                "bbox": {"x_min": 1, "y_min": 10, "x_max": 9, "y_max": 20},
            },
            {
                "text": item.fact.value_raw,
                "block_no": 1,
                "line_no": 0,
                "word_no": 1,
                "bbox": region,
            },
        ]
        lines = [
            {
                "id": "leader",
                "x1": 20,
                "y1": 15,
                "x2": 40,
                "y2": 30,
                "width": 0.2,
                "color": [0, 0, 1],
            },
            {
                "id": "pipe",
                "x1": 30,
                "y1": 30,
                "x2": 80,
                "y2": 30,
                "width": 1,
                "color": [0, 0, 1],
            },
        ]
        pages.append(
            {
                "page_number": item.page,
                "extracted_text": item.fact.evidence_text,
                "page_facts": {
                    **facts_payload(),
                    "document_facts": [asdict(item.fact)],
                },
                "text_words": words,
                "graphic_lines": lines,
            }
        )
    original = candidate(*observations)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/internal/v1/document-context/check-consistency",
            json={
                "enabled": True,
                "document_id": str(uuid4()),
                "pages": pages,
                "page_checks": [
                    {
                        "page_number": 1,
                        "result": {
                            "summary": "",
                            "metrics": {},
                            "findings": [
                                _finding_draft_payload(original).model_dump(mode="json")
                            ],
                        },
                    }
                ],
            },
        )
        assert response.status_code == 200, response.text
        findings = response.json()["items"][0]["result"]["findings"]
        assert len(findings) == 1 and findings[0]["status"] == "needs_review"
        assertion = next(
            origin
            for origin in findings[0]["origin_assertions"]
            if origin.get("origin") == "engineering_assertion"
        )
        assert all(
            item["fact"]["geometry_binding"]["status"] == "bound"
            for item in assertion["observations"]
        )
        assert {
            item["fact"]["geometry_binding"]["page"]
            for item in assertion["observations"]
        } == {1, 2}
        result = await client.post(
            "/internal/v1/findings/finalize",
            json={"findings": findings, "experience_by_finding": {}},
        )
        assert result.status_code == 200, result.text
        assert result.json()["metrics"]["vlm_call_count"] == 0
        assert result.json()["findings"][0]["status"] == "needs_review"
