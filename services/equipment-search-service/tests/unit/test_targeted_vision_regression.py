# services/equipment-search-service/tests/unit/test_targeted_vision_regression.py

"""Адресное чтение сложной страницы, пустые факты и диагностика GPU-бюджета."""

import json

import httpx
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


async def test_vision_uses_selected_page_and_keeps_interface_out_of_execution():
    """Подтверждение маркировки не требует придуманных характеристик; выбран нужный лист."""
    requests = []

    def handler(request):
        """Растр и ответ VLM контролируются без обращения к GPU."""
        requests.append(request)
        if request.url.path.endswith("render-page"):
            assert b"\r\n7\r\n" in request.content
            return httpx.Response(200, json={"image_base64": "PNG"})
        payload = json.loads(request.content)
        assert payload["page"] == 7
        assert payload["variant"] == ""
        assert payload["manufacturer_aliases"] == ["Болид", "bolid"]
        return httpx.Response(
            200,
            json={
                "identified": True,
                "facts": [],
                "metrics": {"done_reason": "cache_hit"},
            },
        )

    budget = EquipmentVisionBudget(2)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await TargetedVisionInspector(
            client, "http://document", "http://analysis"
        ).inspect(
            EquipmentIdentity("Болид", "Сигнал-10", "2RS485", variant_kind="interface"),
            DownloadedDocument(b"%PDF", "https://bolid.ru/doc.pdf", "application/pdf"),
            DocumentInspection(
                False,
                pages=((1, ""), (7, "")),
                requires_vision=True,
                manufacturer_aliases=("Болид", "bolid"),
                vision_pages=(7,),
                reason_code="variant_mismatch",
            ),
            budget=budget,
        )
    assert len(requests) == 2
    assert result.applicable and not result.requires_vision
    assert result.reason_code == "" and result.vision_facts == ()
    assert budget.remaining == 2


async def test_empty_vision_budget_has_machine_code_and_zero_http():
    """Исчерпание квоты видно в диагностике; даже растр не запрашивается."""

    def unexpected(request):
        """Любой HTTP нарушает нулевой бюджет."""
        raise AssertionError("HTTP при исчерпанном VLM бюджете")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        result = await TargetedVisionInspector(
            client, "http://document", "http://analysis"
        ).inspect(
            EquipmentIdentity("Болид", "РИП-12"),
            DownloadedDocument(b"%PDF", "https://bolid.ru/doc.pdf", "application/pdf"),
            DocumentInspection(False, requires_vision=True),
            budget=EquipmentVisionBudget(0),
        )
    assert not result.applicable and result.reason_code == "vision_budget_exhausted"
