# tests/functional/test_equipment_alarm_pdf.py

"""Регрессия реального двухстраничного PDF: бренд не угадывается, порт исключён."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fitz
import pytest
from pdrd_analysis_service.application.use_cases.understanding import UnderstandPage
from pdrd_analysis_service.domain.equipment import build_inventory

PDF = Path(__file__).parents[1] / "fixtures/equipment/security-alarm-two-pages.pdf"


@pytest.mark.parametrize("document_context", [False, True])
async def test_actual_alarm_pdf_does_not_invent_manufacturer_or_search_ports(
    document_context,
):
    """Два листа дают шесть моделей без производителя и сохраняют исходные области."""
    pages = []
    models = (
        ("Сигнал-10", "2RS485"),
        ("РИП-12", "исп. 51"),
        ("ИО 102-29", "Эстет-Сейф"),
        ("ИО 329-3", "Арфа"),
        ("ИО 309-30", "Пирон-Ш"),
        ("Маяк-ШС1", ""),
    )
    with fitz.open(PDF) as document:
        assert len(document) == 2
        for number, page in enumerate(document, 1):
            text = page.get_text("text")
            records = [
                {
                    "manufacturer": "Рубеж",
                    "manufacturer_evidence_text": "Рубеж " + model,
                    "model": model,
                    "variant": variant,
                    "confidence": 0.95,
                    "status": "resolved",
                    "evidence_text": model,
                    "object_ref": "object-" + model,
                    "visual_regions": [
                        {"x_min": 100, "y_min": 100, "x_max": 150, "y_max": 150}
                    ],
                    "parameters": [],
                }
                for model, variant in models
            ]
            records += [
                {
                    "manufacturer": "Сигнал-10",
                    "model": "2RS485",
                    "confidence": 0.95,
                    "status": "resolved",
                    "evidence_text": "Сигнал-10 2RS485",
                },
                {
                    "manufacturer": "",
                    "model": "Touch Memory",
                    "equipment_type": "Порт",
                    "status": "resolved",
                    "confidence": 0.95,
                },
            ]
            vision = SimpleNamespace(
                generate_json=AsyncMock(
                    return_value=SimpleNamespace(
                        payload={"equipment_identities": records},
                        metrics=None,
                    )
                )
            )
            facts, _ = await UnderstandPage(vision, num_predict=512).execute(
                page_number=number,
                heuristic_page_type="schematic",
                extracted_text=text,
                image_bytes=page.get_pixmap().tobytes("png"),
                use_document_context=document_context,
                use_equipment_web_search=True,
            )
            pages.append(
                {
                    "page_number": number,
                    "facts": {"equipment_identities": list(facts.equipment_identities)},
                }
            )
    inventory = build_inventory(pages)
    assert len(inventory) == 6
    assert {(item.model, item.variant) for item in inventory} == set(models)
    assert all(
        item.manufacturer == "" and item.status == "needs_review" for item in inventory
    )
    assert all({part.page for part in item.occurrences} == {1, 2} for item in inventory)
    assert all(
        any(part.visual_regions for part in item.occurrences) for item in inventory
    )
