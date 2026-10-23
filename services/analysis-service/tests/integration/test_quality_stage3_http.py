# services/analysis-service/tests/integration/test_quality_stage3_http.py

"""31 физическая страница → проверка документа → HTTP Finalization без подсказки H16."""

import json
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
from pdrd_analysis_service.application.use_cases.document_context import (
    CheckCrossPageConsistency,
)
from pdrd_analysis_service.application.use_cases.finalization import FinalizeFindings
from pdrd_analysis_service.domain.engineering_facts import merge_text_engineering_facts
from pdrd_analysis_service.domain.visual_specification import visual_specification_facts
from pdrd_analysis_service.transport.http.document_context_routes import _options

from tests.functional.test_quality_completeness import source_pages

from ..unit.test_quality_stage3_reliability import graphic_rows
from .test_http_api import build_app, facts_payload


class NoDecisions:
    """Пропуск свободного решения не должен уничтожать доказанный табличный конфликт."""

    def __init__(self):
        """Считает только действительно выполненные модельные пакеты."""
        self.calls = 0

    async def generate_json(self, **request):
        """Не создаёт и не подтверждает ни одного готового замечания."""
        self.calls += 1
        return SimpleNamespace(payload={"decisions": []})


def control_pages():
    """Слова исходных таблиц дополнены явно помеченными контрольными строками поставки."""
    root = Path(__file__).parents[4] / "tests/fixtures/engineering"
    data = json.loads((root / "ios2-calculation-continuation.json").read_text("utf-8"))[
        "pages"
    ]
    page30 = json.loads((root / "ios2-calculation-page30.json").read_text("utf-8"))
    data.append(
        {"page": 30, "text": page30["text"], "text_words": page30["text_words"]}
    )
    source = {
        p.page: {"text": p.text, "facts": [asdict(f) for f in p.facts]}
        for p in source_pages()
    }
    for p in data:
        source[p["page"]] = {
            **source.get(p["page"], {}),
            "text": p["text"],
            "text_words": p["text_words"],
        }
    source[26] = {
        "text": "",
        "facts": [asdict(f) for f in visual_specification_facts(graphic_rows(), 26)],
    }
    return [
        {
            "page_number": page,
            "extracted_text": source.get(page, {}).get(
                "text", "Контрольный лист без исходных измерений"
            ),
            "text_words": source.get(page, {}).get("text_words", []),
            "page_facts": {
                **facts_payload(),
                "document_facts": source.get(page, {}).get("facts", [])
                + [
                    asdict(f)
                    for f in merge_text_engineering_facts(
                        (), source.get(page, {}).get("text", ""), page
                    )
                ],
            },
        }
        for page in range(1, 32)
    ]


async def test_31_pages_http_survive_missing_model_decisions_and_preserve_h09_h16_h24_h10():
    """Реальные DTO, парсер таблиц, аудит и Finalization; модель не даёт готовых ответов."""
    app, cross_model, final_model = build_app(), NoDecisions(), NoDecisions()
    app.state.container = replace(
        app.state.container,
        check_cross_page_consistency=CheckCrossPageConsistency(
            cross_model, _options(app.state.container)
        ),
        finalize_findings=FinalizeFindings(final_model, 4000, 8, 1000, 0.5),
    )
    pages = control_pages()
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
                        "page_number": p["page_number"],
                        "result": {"summary": "", "findings": [], "metrics": {}},
                    }
                    for p in pages
                ],
            },
        )
        assert response.status_code == 200, response.text
        checked = response.json()
        assert len(checked["items"]) == 31
        final = await client.post(
            "/internal/v1/findings/finalize",
            json={"findings": checked["findings"], "experience_by_finding": {}},
        )
        assert final.status_code == 200, final.text
    findings = final.json()["findings"]
    h16 = next(
        f for f in findings if "0,5205" in f["comment"] and "0,205" in f["comment"]
    )
    assert h16["status"] == "needs_review"
    assert {loc["page"] for loc in h16["evidence_locations"]} == {15, 30}
    assert all(loc["visual_regions"] for loc in h16["evidence_locations"])
    h09 = [
        f
        for f in findings
        if "водоотведение" in f["comment"] and "водоснабжение" in f["comment"]
    ]
    assert len(h09) == 1
    assert {loc["page"] for loc in h09[0]["evidence_locations"]} >= {8, 13, 14}
    assert any(
        "таблица 2" in f["comment"] and "таблица 3" in f["comment"] for f in findings
    )
    h10 = next(f for f in findings if "количество поставляемой трубы" in f["comment"])
    assert h10["status"] == "needs_review"
    assert "147" in h10["comment"] and "150" in h10["comment"]
    assert final_model.calls == 0
    assert final.json()["metrics"]["candidate_fates"]["counts"] == {
        "saved": len(findings)
    }
    record = next(
        r
        for r in checked["diagnostics"]["candidates"]
        if r.get("finding_id") == h16["finding_id"]
    )
    assert record["disposition"] == "saved"
