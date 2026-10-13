# tests/functional/test_equipment_specification_flow.py

"""Реальный PDF проходит Document HTTP, Analysis stage, EQ comparison и JS workflow."""

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pdrd_analysis_service.application.use_cases.understanding import UnderstandPage
from pdrd_analysis_service.core.container import build_container as analysis_container
from pdrd_analysis_service.domain.analysis import GenerationMetrics, GenerationResult
from pdrd_analysis_service.domain.equipment import build_inventory
from pdrd_analysis_service.main import create_app as analysis_app
from pdrd_analysis_service.transport.http.schemas import PageFactsPayload
from pdrd_document_service.application.use_cases.extract import ExtractPdfDocument
from pdrd_document_service.core.container import build_container as document_container
from pdrd_document_service.infrastructure.pdf.pymupdf import PyMuPdfReader
from pdrd_document_service.main import create_app as document_app

ROOT = Path(__file__).parents[2]
PDF = ROOT / "tests/fixtures/equipment/ios-4-1-7.pdf"
WORKFLOW = json.loads(
    (ROOT / "n8n/workflows/analysis-v2-pdf.json").read_text(encoding="utf-8")
)
NODES = {node["name"]: node for node in WORKFLOW["nodes"]}
RUNNER = r"""
const fs = require('node:fs'); const vm = require('node:vm');
const data = JSON.parse(fs.readFileSync(0, 'utf8'));
const wrap = (items) => items.map(json => ({json}));
const context = {$json: data.current, $itemIndex: 0,
  $input: {first: () => ({json:data.current}), all: () => wrap(data.items ?? [])},
  $: name => ({first: () => ({json:(data.parents[name] ?? [{}])[0]}),
              all: () => wrap(data.parents[name] ?? [{}])})};
process.stdout.write(JSON.stringify(vm.runInNewContext(data.code, context, {timeout:2000})));
"""


def node(code, *, current=None, parents=None, items=None):
    """Исполняет настоящий JS узла или выражение HTTP body, сохраняя Unicode."""
    result = subprocess.run(
        ["node", "-e", RUNNER],
        input=json.dumps(
            {
                "code": code,
                "current": current or {},
                "parents": parents or {},
                "items": items or [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def code(name):
    """Оборачивает JS Code-node без изменения его содержимого."""
    return "(() => {\n" + NODES[name]["parameters"]["jsCode"] + "\n})()"


def provider():
    """Контролирует схемы; проверка настоящего GPU вынесена в отдельный acceptance script."""

    async def generate(**request):
        """Сохраняет переданные строки и обычные нормативные запросы страницы."""
        if request["stage"].startswith("specification_rows:"):
            inputs = json.loads(request["prompt"].split("Исходные строки: ")[1])
            fields = request["schema"]["properties"]["items"]["items"]["properties"]
            payload = {
                "items": [
                    {field: item.get(field, "") for field in fields} for item in inputs
                ]
            }
        else:
            payload = {
                "discipline": "ТМ",
                "page_type": "drawing",
                "summary": "Лист проекта",
                "objects": ["Контур"],
                "connections": [],
                "labels": [],
                "normative_queries": ["Требования к котельной"],
                "document_facts": [],
                "equipment_identities": [],
            }
        return GenerationResult(
            payload,
            GenerationMetrics(1, "stop", request["num_predict"], 1, 0, 100, 50, 300, 0),
        )

    return SimpleNamespace(generate_json=AsyncMock(side_effect=generate))


@pytest.fixture(scope="module")
def extracted():
    """Получает все 31 страницы через настоящий multipart endpoint и production reader."""
    container = document_container()
    reader = PyMuPdfReader(render_max_side=500, text_limit=12000)
    container = replace(
        container, extract_pdf=ExtractPdfDocument(reader, 10_000_000, 200)
    )
    with TestClient(document_app(container)) as client:
        response = client.post(
            "/internal/v1/pdf/extract",
            files={"file": ("ios.pdf", PDF.read_bytes(), "application/pdf")},
            data={"pages": "1-31", "include_equipment_specification": "true"},
        )
    assert response.status_code == 200, response.text
    return response.json()


def stage_body(extracted, context):
    """Исполняет body n8n: спецификация берётся из исходного extraction, даже после compact."""
    items = [
        {
            "page_number": page["page_number"],
            "heuristic_page_type": page["page_type"],
            "extracted_text": page["text"],
            "use_document_context": context,
            "use_equipment_web_search": True,
        }
        for page in extracted["pages"]
    ]
    body = NODES["Understand Pages Stage"]["parameters"]["body"]
    expression = body[body.index("{{") + 2 : body.rindex("}}")].strip()
    encoded = node(
        expression,
        current={"document_id": str(uuid4()), "items": items},
        parents={"Document Extract PDF": [extracted]},
    )
    return json.loads(encoded)


@pytest.mark.parametrize("context", [False, True])
async def test_all_31_pages_and_specification_survive_http_and_final_workflow(
    extracted, context
):
    """N/T/U-факты продолжаются; все 255 строк и 131 позиция доходят до итогового EQ JSON."""
    vision = provider()
    container = replace(
        analysis_container(),
        understand_page=UnderstandPage(vision, 3200),
        analysis_progress_probe=None,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=analysis_app(container)),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/internal/v1/stages/understand-pages", json=stage_body(extracted, context)
        )
        assert response.status_code == 200, response.text
        pages = [
            {"page_number": item["page_number"], "facts": item["result"]["facts"]}
            for item in response.json()["items"]
        ]
        search_inputs = node(
            code("Build Equipment Search Inputs"),
            parents={"Understand Pages Stage": [response.json()]},
        )[0]["json"]["identities"]
        keys = [
            (item["manufacturer"], item["model"], item["variant"], item["article"])
            for item in search_inputs
        ]
        assert len(keys) == len(set(keys)) < 131
        assert set(keys) == {
            (item["manufacturer"], item["model"], item["variant"], item["article"])
            for page in pages
            for item in page["facts"].get("equipment_identities", [])
            if item["model"]
        }
        assert [page["page_number"] for page in pages] == list(range(1, 32))
        assert all(
            PageFactsPayload.model_validate(page["facts"]).to_domain().normative_queries
            for page in pages
        )
        for request in vision.generate_json.await_args_list:
            kwargs = request.kwargs
            if kwargs["stage"].startswith("page_understanding:"):
                number = int(kwargs["stage"].split(":")[1])
                assert ("equipment_identities" in kwargs["schema"]["properties"]) is (
                    number < 24
                )
                assert ("document_facts" in kwargs["schema"]["properties"]) is context
        compared = await client.post(
            "/internal/v1/stages/compare-equipment",
            json={"pages": pages, "search": {"status": "incomplete", "results": []}},
        )
        assert compared.status_code == 200, compared.text
    comparison = compared.json()
    report = comparison["specification"]
    assert (report["item_count"], report["raw_row_count"], report["pages"]) == (
        131,
        255,
        list(range(24, 32)),
    )
    assert report["coverage_complete"]
    assert len(report["structured_items"]) == len(report["items"]) == 131
    assert all(
        set(item) == {"Caption", "Units", "Quantity", "UserComment1", "Under"}
        for item in report["items"]
    )
    inventory = build_inventory(pages)
    assert len(inventory) < 131
    assert sum(len(item.occurrences) for item in inventory) == 131
    assert all(
        occurrence.source_row_ids and occurrence.quantity
        for item in inventory
        for occurrence in item.occurrences
    )
    merged = node(
        code("Merge Equipment Candidates"),
        current=comparison,
        parents={"Collect Equipment Candidates": [{"pages": pages}]},
    )
    final = node(
        code("Aggregate PDF Result"),
        items=merged and [item["json"] for item in merged],
        parents={
            "POST /analysis/v2/pdf": [
                {
                    "body": {
                        "use_equipment_web_search": "true",
                        "use_document_context": context,
                    }
                }
            ],
            "Merge Equipment Candidates": [merged[0]["json"]],
        },
    )[0]["json"]
    assert final["status"] == "completed" and final["analyzed_pages"] == 31
    assert final["equipment_check"]["specification"] == report
    assert len(final["pages"]) == 31


async def test_bad_specification_is_http_422_before_any_gpu(extracted):
    """Недостающая позиция отклоняется независимо от переданного флага полноты."""
    payload = stage_body(extracted, False)
    item = payload["items"][24]
    item["equipment_specification"]["items"].pop()
    vision = provider()
    container = replace(
        analysis_container(), understand_page=UnderstandPage(vision, 3200)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=analysis_app(container)),
        base_url="http://test",
    ) as client:
        response = await client.post("/internal/v1/pages/understand", json=item)
    assert response.status_code == 422
    vision.generate_json.assert_not_awaited()


async def test_stage_cancellation_between_refinement_and_main_is_http_409(extracted):
    """Отмена после короткого уточнения не начинает следующий GPU-запрос."""
    payload = stage_body(extracted, False)
    item = payload["items"][26]
    assert any(row["needs_review"] for row in item["equipment_specification"]["items"])
    payload["items"] = [item]
    probe = SimpleNamespace(is_cancelled=AsyncMock(side_effect=[False, False, True]))
    vision = provider()
    container = replace(
        analysis_container(),
        understand_page=UnderstandPage(vision, 3200),
        analysis_progress_probe=probe,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=analysis_app(container)),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/internal/v1/stages/understand-pages", json=payload
        )
    assert (
        response.status_code == 409
        and response.json()["detail"]["code"] == "analysis_cancelled"
    )
    assert vision.generate_json.await_count == 1
