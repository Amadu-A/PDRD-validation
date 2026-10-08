# services/analysis-service/tests/integration/test_equipment_vision_http.py

"""HTTP контракт адресного EQ VLM с ограничением изображения и идентификации."""

import base64
from types import SimpleNamespace

import httpx
import pytest
from pdrd_analysis_service.application.equipment_vision import ExtractEquipmentVision
from pdrd_analysis_service.core.settings import Settings
from pdrd_analysis_service.domain.analysis import GenerationMetrics, GenerationResult
from pdrd_analysis_service.main import create_app
from pdrd_analysis_service.transport.http.dependencies import get_container


class Vision:
    """Возвращает точную маркировку и серверные метрики без GPU."""

    calls = 0

    async def generate_json(self, **kwargs):
        """Проверяет получение PNG только выбранной физической страницы."""
        self.calls += 1
        assert kwargs["image_bytes"].startswith(b"\x89PNG\r\n\x1a\n")
        return GenerationResult(
            payload={
                "manufacturer": "IEK",
                "model": "KM20",
                "variant": "",
                "confidence": 0.9,
                "facts": [],
            },
            metrics=GenerationMetrics(1, "stop", 1000, 5.0, 0.0, 20, 10, 10, 0),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override, expected",
    [
        ({}, 200),
        ({"page": 41}, 422),
        ({"image_base64": "invalid"}, 422),
        ({"image_base64": base64.b64encode(b"not PNG").decode()}, 422),
    ],
)
async def test_equipment_vision_http_limits_and_metrics(override, expected) -> None:
    """Невалидный запрос отклоняется до VLM, корректный сохраняет токены."""
    vision = Vision()
    container = SimpleNamespace(
        settings=Settings(_env_file=None),
        extract_equipment_vision=ExtractEquipmentVision(vision),
    )
    app = create_app(container)
    app.dependency_overrides[get_container] = lambda: container
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://analysis",
    ) as client:
        body = {
            "manufacturer": "IEK",
            "model": "KM20",
            "page": 1,
            "image_base64": base64.b64encode(b"\x89PNG\r\n\x1a\n").decode(),
        }
        response = await client.post(
            "/internal/v1/stages/equipment-document-vision", json={**body, **override}
        )
    assert response.status_code == expected
    assert vision.calls == (1 if expected == 200 else 0)
    if expected == 200:
        assert response.json()["identified"] is True
        assert response.json()["metrics"]["prompt_eval_count"] == 20
