# services/equipment-search-service/tests/unit/test_vision_inspection.py

"""Проверяет ограничение адресного VLM, отказ сервиса и отмену."""

import asyncio
import json

import httpx
import pytest
from pdrd_equipment_search_service.application.vision_budget import (
    EquipmentVisionBudget,
)
from pdrd_equipment_search_service.domain.equipment import (
    DocumentInspection,
    DownloadedDocument,
    EquipmentIdentity,
)
from pdrd_equipment_search_service.infrastructure.vision_inspection import (
    TargetedVisionInspector,
)

IDENTITY = EquipmentIdentity("MEAN WELL", "DRC-100B")
DOCUMENT = DownloadedDocument(
    b"%PDF-1.7", "https://www.meanwell.com/scan.pdf", "application/pdf"
)
SCAN = DocumentInspection(
    False, pages=((1, ""), (2, "")), requires_vision=True, reason="Скан"
)


@pytest.mark.asyncio
async def test_targeted_scan_stops_at_identified_page_and_uses_job_budget() -> None:
    """Одна успешная страница прекращает VLM; следующий объект делит бюджет."""
    requests = []

    def respond(request):
        """Имитирует Document и Analysis HTTP контракты."""
        requests.append(request)
        if "render-page" in request.url.path:
            return httpx.Response(200, json={"image_base64": "UE5H"})
        data = json.loads(request.content)
        assert data["model"] == "DRC-100B"
        return httpx.Response(
            200, json={"identified": True, "facts": [{"snippet": "24 V"}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        adapter = TargetedVisionInspector(client, "http://document", "http://analysis")
        budget = EquipmentVisionBudget(1)
        result = await adapter.inspect(IDENTITY, DOCUMENT, SCAN, budget=budget)
        second = await adapter.inspect(IDENTITY, DOCUMENT, SCAN, budget=budget)
    assert result.applicable and result.vision_facts[0]["page"] == 1
    assert not second.applicable and "бюджет" in second.reason
    assert len(requests) == 2 and budget.remaining == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [{"identified": False, "facts": []}, {"identified": True, "facts": [None]}],
)
async def test_unidentified_or_invalid_scan_is_not_evidence(payload) -> None:
    """Чужая модель и некорректные факты не допускают snapshot."""
    calls = []

    def respond(request):
        """Возвращает ответ по каждому из двух выбранных листов."""
        calls.append(request)
        return httpx.Response(
            200,
            json={"image_base64": "UE5H"}
            if "render-page" in request.url.path
            else payload,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await TargetedVisionInspector(
            client, "http://document", "http://analysis"
        ).inspect(
            IDENTITY,
            DOCUMENT,
            SCAN,
            budget=EquipmentVisionBudget(2),
        )
    assert not result.applicable and not result.vision_facts
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_failed_raster_keeps_warning_without_spending_vlm() -> None:
    """Отказ растеризации не расходует GPU-бюджет и не скрывается."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(503))
    ) as client:
        budget = EquipmentVisionBudget(2)
        result = await TargetedVisionInspector(
            client, "http://document", "http://analysis"
        ).inspect(
            IDENTITY,
            DOCUMENT,
            SCAN,
            budget=budget,
        )
    assert not result.applicable and "недоступно" in result.reason
    assert budget.remaining == 2


@pytest.mark.asyncio
async def test_cancellation_prevents_raster_and_gpu_calls() -> None:
    """После отмены не запускается даже подготовка страницы."""

    async def stopped():
        """Возвращает установленную отмену задания."""
        return True

    def unexpected(request):
        """Любой HTTP запрос нарушает контракт отмены."""
        raise AssertionError("HTTP после отмены")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        with pytest.raises(asyncio.CancelledError):
            await TargetedVisionInspector(
                client, "http://document", "http://analysis"
            ).inspect(
                IDENTITY,
                DOCUMENT,
                SCAN,
                budget=EquipmentVisionBudget(2),
                should_stop=stopped,
            )
