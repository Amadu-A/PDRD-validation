# services/analysis-service/tests/unit/test_equipment_vision.py

"""Проверяет адресное визуальное извлечение без автоматического вывода о нарушении."""

from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from pdrd_analysis_service.application.equipment_vision import (
    ExtractEquipmentVision,
)


@dataclass
class FakeVision:
    """Возвращает контролируемый структурированный ответ."""

    payload: dict
    calls: int = 0
    request: dict | None = None

    async def generate_json(self, **kwargs):
        """Запоминает один адресный VLM вызов."""
        self.calls += 1
        self.request = kwargs
        return SimpleNamespace(payload=self.payload)


@pytest.mark.asyncio
async def test_exact_model_on_one_page_produces_review_facts() -> None:
    """Точная маркировка сохраняет страницу, а лимит фактов остаётся малым."""
    vision = FakeVision(
        {
            "manufacturer": "MEAN WELL",
            "model": "DRC-100B",
            "variant": "",
            "confidence": 0.92,
            "facts": [
                {
                    "property_name": "Output voltage",
                    "value_raw": "21...29",
                    "unit_raw": "V",
                    "role": "output",
                    "current_type": "DC",
                    "phase": "",
                    "snippet": "DRC-100B Output voltage 21...29 V DC",
                }
            ],
        }
    )
    result = await ExtractEquipmentVision(vision).execute(
        manufacturer="MEAN WELL",
        model="DRC-100B",
        variant="",
        properties=("output_voltage",),
        page=2,
        image_bytes=b"PNG",
    )
    assert result["identified"] is True
    assert result["facts"][0]["page"] == 2
    assert vision.calls == 1
    assert vision.request["image_bytes"] == b"PNG"
    assert vision.request["schema"]["properties"]["facts"]["maxItems"] == 8


@pytest.mark.asyncio
async def test_neighbor_model_cannot_validate_scanned_document() -> None:
    """Ответ по соседней модели не используется как EQ evidence."""
    vision = FakeVision(
        {
            "manufacturer": "MEAN WELL",
            "model": "DRC-100A",
            "variant": "",
            "confidence": 0.99,
            "facts": [{"property_name": "Output voltage", "value_raw": "12"}],
        }
    )
    result = await ExtractEquipmentVision(vision).execute(
        manufacturer="MEAN WELL",
        model="DRC-100B",
        variant="",
        properties=(),
        page=1,
        image_bytes=b"PNG",
    )
    assert result == {"identified": False, "facts": []}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override",
    [
        {"confidence": "invalid"},
        {"confidence": True},
        {"confidence": 2.0},
        {"facts": None},
        {"variant": "other"},
    ],
)
async def test_malformed_identity_or_payload_cannot_validate_scan(override) -> None:
    """Исполнение и невалидная структура не подтверждают применимость скана."""
    payload = {
        "manufacturer": "MEAN WELL",
        "model": "DRC-100B",
        "variant": "",
        "confidence": 0.9,
        "facts": [],
    }
    result = await ExtractEquipmentVision(FakeVision({**payload, **override})).execute(
        manufacturer="MEAN WELL",
        model="DRC-100B",
        variant="",
        properties=(),
        page=1,
        image_bytes=b"PNG",
    )
    assert result == {"identified": False, "facts": []}
