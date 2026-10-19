# services/analysis-service/tests/integration/test_requirement_plan_http.py

"""Независимые проверки через штатные HTTP-этапы, без готовых модельных findings."""

from dataclasses import asdict, replace
from datetime import UTC, datetime

import httpx
import pytest
from pdrd_analysis_service.application.use_cases.finalization import FinalizeFindings
from pdrd_analysis_service.application.use_cases.normative import (
    BuildNormativeQueries,
    CheckPageAgainstNorms,
)
from pdrd_analysis_service.domain.analysis import (
    GenerationMetrics,
    GenerationResult,
    PageFacts,
)

from ..unit.test_requirement_plan import CODE, observed, requirement, status
from .test_http_api import build_app, encoded_image, facts_payload


class NoCandidates:
    """Существующий Check Norms ничего не обнаружил; новый маршрут не получает готовое замечание."""

    def __init__(self):
        """Считает фактические обращения для A/B одного и того же этапа."""
        self.calls = 0

    async def generate_json(self, **request):
        """Только один штатный вызов обнаружения, без нового обхода по требованиям."""
        assert request["stage"].startswith("normative_check:")
        self.calls += 1
        return GenerationResult(
            {"summary": "", "violations": []},
            GenerationMetrics(1, "stop", 4000, 1, 0, 10, 10, 0, 0),
        )


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("value", ["4", "6"])
async def test_http_discovery_and_successful_checks_survive_zero_model_candidates(
    enabled, value
):
    """Проверка N работает независимо от findings; выполненные требования сохраняются в metrics."""
    app, model = build_app(), NoCandidates()
    app.state.container = replace(
        app.state.container,
        check_page_against_norms=CheckPageAgainstNorms(
            model, 4000, 20, 1000, requirement_plan_enabled=enabled
        ),
        finalize_findings=FinalizeFindings(model, 4000, 8, 1000, 0.5),
    )
    item = observed(value)
    sources = (
        requirement(),
        status(
            f"Статус {CODE} на {datetime.now(UTC).date()}: действует; требования обязательны."
        ),
    )
    source_payload = [
        {
            "source_id": s.source_id,
            "point_id": s.point_id,
            "score": 0.8,
            "source_file": s.source_file,
            "page": s.page,
            "text": s.text,
            "chunk_index": 0,
        }
        for s in sources
    ]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/internal/v1/pages/check-norms",
            json={
                "page_number": 1,
                "extracted_text": item.fact.evidence_text,
                "page_facts": {
                    **facts_payload(),
                    "document_facts": [asdict(item.fact)],
                },
                "normative_sources": source_payload,
                "image_base64": encoded_image(),
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert model.calls == 1
        if not enabled:
            assert "requirement_plan" not in body["metrics"] and not body["findings"]
            return
        plan = body["metrics"]["requirement_plan"]
        assert plan["coverage"]["completed"] == 1
        assert plan["coverage"]["new_vlm_calls"] == 0
        assert len(body["findings"]) == (1 if value == "4" else 0)
        final = await client.post(
            "/internal/v1/findings/finalize",
            json={
                "findings": body["findings"],
                "experience_by_finding": {},
            },
        )
        assert final.status_code == 200, final.text
        assert final.json()["metrics"]["vlm_call_count"] == 0
        if value == "4":
            finding = final.json()["findings"][0]
            assert finding["status"] == "confirmed"
            assert {s["source_id"] for s in finding["basis_sources"]} == {"N1", "N2"}
            assert finding["evidence_locations"][0]["page"] == 1


def test_queries_append_bounded_independent_topics_and_keep_original_prefix():
    """Приоритет раздела не ограничивает корпус и не вытесняет действующие запросы."""
    facts = PageFacts(
        "Водоснабжение", "text", "", (), (), (), tuple(f"query-{i}" for i in range(10))
    )
    text = (
        "Гидранты, пожарный запас резервуаров, водопровод, трубопроводы на основании."
    )
    baseline = BuildNormativeQueries().execute(page_facts=facts, extracted_text=text)
    current = BuildNormativeQueries(
        requirement_plan_enabled=True,
        priority_documents_by_section={"water": ["СП 8.13130"]},
    ).execute(page_facts=facts, extracted_text=text, section_id="water")
    assert current[: len(baseline)] == baseline
    assert 0 < len(current) - len(baseline) <= 4
    assert all(
        "СП 8.13130" in query and "остальные" in query
        for query in current[len(baseline) :]
    )
    other = BuildNormativeQueries(
        requirement_plan_enabled=True,
        priority_documents_by_section={"water": ["unique-priority"]},
    ).execute(page_facts=facts, extracted_text=text, section_id="other")
    assert all("unique-priority" not in query for query in other)


async def test_cancelled_job_does_not_run_new_plan_or_discovery():
    """Отмена до проверки не открывает новый цикл генерации по положениям нормы."""
    from pdrd_analysis_service.application.vision_execution import (
        AnalysisCancelledError,
    )

    async def cancelled():
        """Воспроизводит уже отменённое задание без сети и базы данных."""
        raise AnalysisCancelledError("Задание отменено")

    model = NoCandidates()
    with pytest.raises(AnalysisCancelledError):
        await CheckPageAgainstNorms(
            model, 4000, 20, 1000, requirement_plan_enabled=True
        ).execute(
            page_number=1,
            extracted_text="",
            page_facts=PageFacts("", "text", "", (), (), (), ()),
            normative_sources=(),
            image_bytes=b"image",
            check_cancellation=cancelled,
        )
    assert model.calls == 0
