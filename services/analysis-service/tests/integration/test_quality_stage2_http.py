# services/analysis-service/tests/integration/test_quality_stage2_http.py

"""HTTP-путь из 31 страницы: программное назначение, multi-page evidence и Finalization."""

from dataclasses import asdict, replace
from uuid import uuid4

import httpx
from pdrd_analysis_service.application.use_cases.document_context import (
    CheckCrossPageConsistency,
)
from pdrd_analysis_service.application.use_cases.finalization import FinalizeFindings
from pdrd_analysis_service.domain.engineering_facts import explicit_system_purposes
from pdrd_analysis_service.transport.http.document_context_routes import _options

from .test_engineering_program_http import ForbiddenVision
from .test_http_api import build_app, facts_payload


async def test_31_pages_reach_result_without_generation_or_lost_source_pages():
    """Порт GPU запрещён, все физические страницы и исходные ID проходят настоящий ASGI."""
    app, model = build_app(), ForbiddenVision()
    app.state.container = replace(
        app.state.container,
        check_cross_page_consistency=CheckCrossPageConsistency(
            model, _options(app.state.container)
        ),
        finalize_findings=FinalizeFindings(model, 2000, 8, 1000, 0.5),
    )
    pages = []
    for n in range(1, 32):
        text = (
            "Система водоотведения S7."
            if n == 13
            else "Система водоснабжения S7."
            if n == 14
            else "Служебная страница."
        )
        facts = facts_payload()
        facts["document_facts"] = [asdict(f) for f in explicit_system_purposes(text)]
        pages.append({"page_number": n, "extracted_text": text, "page_facts": facts})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/internal/v1/document-context/check-consistency",
            json={
                "document_id": str(uuid4()),
                "enabled": True,
                "pages": pages,
                "page_checks": [
                    {
                        "page_number": n,
                        "result": {"summary": "", "metrics": {}, "findings": []},
                    }
                    for n in range(1, 32)
                ],
            },
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert [item["page_number"] for item in result["items"]] == list(range(1, 32))
        findings = [f for item in result["items"] for f in item["result"]["findings"]]
        assert len(findings) == 1
        assert {item["page"] for item in findings[0]["evidence_locations"]} == {13, 14}
        final = await client.post(
            "/internal/v1/findings/finalize",
            json={"findings": findings, "experience_by_finding": {}},
        )
        assert final.status_code == 200, final.text
        assert len(final.json()["findings"]) == 1
        assert final.json()["metrics"]["vlm_call_count"] == 0
        fate = final.json()["metrics"]["candidate_fates"]["candidates"][0]
        assert fate["disposition"] == "saved" and fate["candidate_ids"] == ["C1"]
        assert fate["candidate_ref"] == {
            "collection": "findings",
            "finding_id": final.json()["findings"][0]["finding_id"],
        }
        assert len(final.json()["findings"][0]["document_context_basis_sources"]) == 2
        assert "candidate" not in fate
