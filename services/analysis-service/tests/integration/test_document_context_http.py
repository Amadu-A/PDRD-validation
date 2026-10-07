# services/analysis-service/tests/integration/test_document_context_http.py

"""HTTP-регрессии выбора D и единого межстраничного результата."""

from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from pdrd_analysis_service.application.use_cases.document_context import (
    CheckCrossPageConsistency,
)
from pdrd_analysis_service.application.use_cases.understanding import UnderstandPage
from pdrd_analysis_service.transport.http.document_context_routes import _options

from .test_http_api import (
    FakeVisionModel,
    build_app,
    encoded_image,
    facts_payload,
    normative_payload,
)


def fact_payload(value):
    """Создаёт сопоставимые исходные данные одного помещения."""
    return {
        "kind": "parameter",
        "subject_type": "room",
        "subject_name": "Помещение Б-012",
        "identifier": "Б-012",
        "property_type": "temperature",
        "property_name": "Расчётная температура наружного воздуха",
        "value_raw": value,
        "unit_raw": "°C",
        "scope_system": "ОВ",
        "scope_location": "подвал",
        "scope_operating_mode": "winter_design",
        "scope_condition": "расчётный зимний режим",
        "evidence_text": f"Б-012 {value} °C",
    }


class RecordingVision(FakeVisionModel):
    """Сохраняет ограничения запроса и имитирует лишний D-факт в ответе."""

    async def generate_json(self, **kwargs):
        """Проверяет серверную фильтрацию даже при нарушении схемы моделью."""
        self.schema = kwargs["schema"]
        result = await super().generate_json(**kwargs)
        return replace(
            result, payload={**result.payload, "document_facts": [fact_payload("-37")]}
        )


class ConfirmingVision:
    """Подтверждает проверяемое совпадение объекта и расчётных условий."""

    async def generate_json(self, **kwargs):
        """Возвращает решения для реально переданного набора групп."""
        from types import SimpleNamespace

        ids = kwargs["schema"]["properties"]["decisions"]["items"]["properties"][
            "candidate_id"
        ]["enum"]
        return SimpleNamespace(
            payload={
                "decisions": [
                    {
                        "candidate_id": identity,
                        "status": "confirmed",
                        "reason": "Совпадают объект и условия.",
                    }
                    for identity in ids
                ]
            }
        )


class ForbiddenCrossCheck:
    """Затратный сценарий, который нельзя запускать при выключенном D."""

    async def execute(self, **kwargs):
        """Любой вызов означает нарушение быстрого режима."""
        raise AssertionError("D отключён")


@pytest.mark.parametrize("enabled", [False, True])
async def test_understanding_stage_preserves_document_switch(enabled):
    """Пакетный HTTP-этап запрещает извлечение D при выключенном чекбоксе."""
    app = build_app()
    model = RecordingVision()
    app.state.container = replace(
        app.state.container, understand_page=UnderstandPage(model, 1600)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/internal/v1/stages/understand-pages",
            json={
                "document_id": str(uuid4()),
                "items": [
                    {
                        "page_number": 7,
                        "heuristic_page_type": "scheme",
                        "extracted_text": "Б-012 -37 °C",
                        "image_base64": encoded_image(),
                        "use_document_context": enabled,
                    }
                ],
            },
        )
    assert response.status_code == 200, response.text
    facts = response.json()["items"][0]["result"]["facts"]
    assert len(facts["document_facts"]) == int(enabled)
    assert model.schema["properties"]["document_facts"]["maxItems"] == (
        12 if enabled else 0
    )
    assert facts["objects"] == ["ЩР-1"]


async def test_consistency_http_merges_d_and_n_and_preserves_fast_mode():
    """HTTP возвращает одно замечание D+N, а выключенный режим сохраняет обычные проверки."""
    app = build_app()
    app.state.container = replace(
        app.state.container,
        check_cross_page_consistency=CheckCrossPageConsistency(
            ConfirmingVision(),
            _options(app.state.container),
        ),
    )
    pages = [
        {
            "page_number": number,
            "extracted_text": f"Б-012 {value} °C",
            "page_facts": {**facts_payload(), "document_facts": [fact_payload(value)]},
        }
        for number, value in [(7, "-37"), (10, "-35")]
    ]
    body = {"document_id": str(uuid4()), "pages": pages, "enabled": True}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post(
            "/internal/v1/document-context/check-consistency", json=body
        )
        assert first.status_code == 200, first.text
        (cross,) = first.json()["findings"]
        duplicate = {
            **cross,
            "finding_id": "p7-f1",
            "basis": "СП",
            "basis_sources": [normative_payload()],
            "normative_source_ids": ["N1"],
        }
        page_checks = [
            {
                "page_number": number,
                "result": {
                    "summary": "Проверено",
                    "metrics": {},
                    "findings": [
                        {**duplicate, "page": number, "finding_id": f"p{number}-f1"}
                    ],
                },
            }
            for number in [7, 10]
        ]
        response = await client.post(
            "/internal/v1/document-context/check-consistency",
            json={**body, "page_checks": page_checks},
        )
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        (merged,) = items[0]["result"]["findings"]
        assert items[1]["result"]["findings"] == []
        assert merged["finding_id"] == cross["finding_id"]
        assert merged["source_kinds"] == ["D", "N"]
        assert [item["page"] for item in merged["evidence_locations"]] == [7, 10]
        app.state.container = replace(
            app.state.container, check_cross_page_consistency=ForbiddenCrossCheck()
        )
        disabled = await client.post(
            "/internal/v1/document-context/check-consistency",
            json={**body, "enabled": False, "page_checks": page_checks},
        )
        assert disabled.status_code == 200, disabled.text
        assert disabled.json()["findings"] == []
        assert [
            item["result"]["findings"][0]["finding_id"]
            for item in disabled.json()["items"]
        ] == ["p7-f1", "p10-f1"]
