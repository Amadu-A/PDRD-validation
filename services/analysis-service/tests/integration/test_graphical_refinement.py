# services/analysis-service/tests/integration/test_graphical_refinement.py

"""Проверяет настоящее PIL-преобразование фрагментов и mock VLM без нагрузки GPU."""

from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace

import pytest
from pdrd_analysis_service.application.graphical_refinement import RefineGraphicalFacts
from pdrd_analysis_service.domain.analysis import PageFacts
from pdrd_analysis_service.domain.document_context import document_fact_from_mapping
from pdrd_analysis_service.infrastructure.image_cropper import PilImageCropper
from PIL import Image


class CropVision:
    """Фиксирует короткие запросы и отдаёт читаемый факт в координатах фрагмента."""

    def __init__(self):
        """Создаёт журнал вызовов."""
        self.calls = []

    async def generate_json(self, **request):
        """Возвращает область внутри crop, сохраняя назначение параметра."""
        self.calls.append(request)
        return SimpleNamespace(
            payload={
                "readable": True,
                "value_raw": "12",
                "evidence_text": "S1: 12 м",
                "region": {
                    "x_min": 0,
                    "y_min": 0,
                    "x_max": 1000,
                    "y_max": 1000,
                    "confidence": 0.9,
                },
            }
        )


def _image():
    """Готовит растр с размером, требующим округления границ до пикселей."""
    buffer = BytesIO()
    Image.new("RGB", (101, 203), "white").save(buffer, format="PNG")
    return buffer.getvalue()


async def test_crop_coordinates_and_original_evidence_are_preserved() -> None:
    """Фактические пиксельные границы возвращаются в исходный PDF, исходный факт остаётся."""
    fact = document_fact_from_mapping(
        {
            "kind": "parameter",
            "identifier": "S1",
            "value_raw": "?",
            "evidence_text": "S1: неразборчиво",
            "visual_regions": [
                {
                    "x_min": 201,
                    "y_min": 301,
                    "x_max": 499,
                    "y_max": 599,
                    "confidence": 0.5,
                }
            ],
        }
    )
    assert fact is not None
    facts = PageFacts("ВК", "drawing", "", (), (), (), (), document_facts=(fact,))
    model = CropVision()
    cropper = PilImageCropper()
    result = await RefineGraphicalFacts(model, cropper).execute(
        facts, page_number=7, image_bytes=_image()
    )
    crop = await cropper.crop(_image(), fact.visual_regions[0])
    assert result.document_facts[0] == fact and len(result.document_facts) == 2
    refined = result.document_facts[1]
    assert refined.source_item_id == "p0007-f0001" and refined.source_pages == (7,)
    assert refined.visual_regions == (
        crop.project_region(
            {
                "x_min": 0,
                "y_min": 0,
                "x_max": 1000,
                "y_max": 1000,
                "confidence": 0.9,
            }
        ),
    )
    assert refined.visual_regions[0]["x_min"] == round(20 / 101 * 1000)
    assert model.calls[0]["num_predict"] == 1200
    readable = replace(
        facts, document_facts=(replace(fact, value_raw="12", evidence_text="S1: 12 м"),)
    )
    assert (
        await RefineGraphicalFacts(model, cropper).execute(
            readable, page_number=7, image_bytes=_image()
        )
        == readable
    )
    assert len(model.calls) == 1


@pytest.mark.parametrize(
    "region",
    [
        {"x_min": 500, "y_min": 0, "x_max": 100, "y_max": 1000},
        {"x_min": 0, "y_min": 0, "x_max": 0, "y_max": 1000},
    ],
)
async def test_invalid_crop_does_not_produce_coordinates(region) -> None:
    """Неверная геометрия не превращается в допустимую область исходного листа."""
    with pytest.raises(ValueError):
        await PilImageCropper().crop(_image(), region)
