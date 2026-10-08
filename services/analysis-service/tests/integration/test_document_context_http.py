# services/analysis-service/tests/integration/test_document_context_http.py

"""HTTP-регрессии выбора D и единого межстраничного результата."""

import json
import subprocess
from dataclasses import replace
from pathlib import Path
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


WORKFLOW_PATH = (
    Path(__file__).resolve().parents[4] / "n8n/workflows/analysis-v2-pdf.json"
)
BODY_EXPRESSION_RUNNER = """
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const context = {
  $json: input.current,
  $: () => ({first: () => ({json: {body: {use_document_context: input.enabled}}})}),
};
const body = vm.runInNewContext(input.expression.slice(3, -2), context, {timeout: 1000});
if (typeof body !== 'string' || !body.length) {
  throw new Error('Выражение Body не сформировало JSON-строку.');
}
process.stdout.write(body);
"""


def document_context_workflow_body(current, enabled):
    """Выполняет публикуемое выражение Body без изменения его кода или входа."""
    workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
    node = next(
        node
        for node in workflow["nodes"]
        if node["name"] == "Build Page Document Context"
    )
    parameters = node["parameters"]
    assert parameters["sendBody"]
    assert parameters["contentType"] == "raw"
    assert parameters["rawContentType"] == "application/json"
    assert parameters["body"].startswith("={{") and parameters["body"].endswith("}}")
    result = subprocess.run(
        ["node", "-e", BODY_EXPRESSION_RUNNER],
        input=json.dumps(
            {"current": current, "enabled": enabled, "expression": parameters["body"]},
            ensure_ascii=False,
        ),
        capture_output=True,
        encoding="utf-8",
        check=True,
        timeout=10,
    )
    return result.stdout


@pytest.mark.parametrize("switch", [True, "true", False, "false", None, ""])
async def test_workflow_raw_body_reaches_page_context_api_with_user_switch(switch):
    """Тело из workflow сохраняет страницы и семантику, а API учитывает выбор без ПЗ."""
    request = {
        "pages": [
            {
                "page_number": number,
                "extracted_text": f"Б-012 {value} °C",
                "page_facts": {
                    **facts_payload(),
                    "document_facts": [fact_payload(value)],
                },
                "text_words": [{"text": value, "x0": 10, "y0": 20, "x1": 40, "y1": 30}],
            }
            for number, value in [(7, "-37"), (10, "-35")]
        ],
        "semantic": [
            {"page_number": 7, "sources": [{"source_id": "D-test", "page": 10}]},
            {"page_number": 10, "sources": []},
        ],
    }
    enabled = switch is True or switch == "true"
    body = document_context_workflow_body(request, switch)
    assert json.loads(body) == {**request, "enabled": enabled}
    app = build_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/internal/v1/document-context/pages",
            content=body.encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["page_number"] for item in items] == [7, 10]
    assert all(bool(item["sources"]) == enabled for item in items)
