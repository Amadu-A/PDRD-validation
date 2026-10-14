# services/analysis-service/tests/integration/test_warm_cache_fairness.py

"""Короткая приёмка настоящего файлового кеша и очереди с mock VLM, без GPU."""

import asyncio
from time import perf_counter

from pdrd_analysis_service.application.limited_vision import LimitedVisionModel
from pdrd_analysis_service.application.vision_execution import (
    VisionExecution,
    vision_execution,
)
from pdrd_analysis_service.domain.analysis import GenerationMetrics, GenerationResult
from pdrd_analysis_service.infrastructure.vllm_cache import CachedStructuredVisionModel


class MockVision:
    """Держит холодный запрос и возвращает полное содержимое синтетической страницы."""

    def __init__(self):
        """Готовит сигнал запуска и освобождения холодного запроса."""
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def generate_json(self, **request):
        """Сохраняет три источника и область каждого замечания в ответе провайдера."""
        self.calls += 1
        if request["stage"] == "cold":
            self.started.set()
            await self.release.wait()
        return GenerationResult(
            {
                "page": request["stage"],
                "findings": [
                    {
                        "id": "F1",
                        "sources": ["N1", "T1", "D1"],
                        "evidence": "Исходная строка",
                        "region": [10, 20, 30, 40],
                    }
                ],
            },
            GenerationMetrics(1, "stop", 100, 5, 0, 20, 10, 60, 0),
        )

    async def is_ready(self):
        """Сообщает готовность mock модели."""
        return True


async def test_31_warm_pages_bypass_busy_slot_without_loss(tmp_path) -> None:
    """Тёплый анализ проходит при занятом слоте, сохраняя страницы, замечания и источники."""
    provider = MockVision()
    model = CachedStructuredVisionModel(
        delegate=LimitedVisionModel(provider, max_concurrent=1),
        provider_identity="mock",
        root_path=tmp_path,
        namespace="test",
        ttl_seconds=60,
        enabled=True,
    )

    async def active() -> bool:
        """Оставляет оба анализа активными."""
        return False

    async def call(stage):
        """Использует полный request contract и тот же exact cache key при повторении."""
        return await model.generate_json(
            prompt="fixture", schema={}, num_predict=100, seed=1, stage=stage
        )

    baseline = [await call(f"page:{page}") for page in range(1, 32)]
    with vision_execution(VisionExecution("cold-job", active)):
        cold = asyncio.create_task(call("cold"))
    await asyncio.wait_for(provider.started.wait(), 1)
    started = perf_counter()
    try:
        with vision_execution(VisionExecution("warm-job", active)):
            warmed = await asyncio.wait_for(
                asyncio.gather(*(call(f"page:{page}") for page in range(1, 32))), 2
            )
        assert perf_counter() - started < 2
        assert [item.payload for item in warmed] == [item.payload for item in baseline]
        assert all(
            item.metrics.done_reason == "cache_hit" and item.metrics.slot_wait_ms == 0
            for item in warmed
        )
        assert provider.calls == 32 and not cold.done()
    finally:
        provider.release.set()
        await asyncio.wait_for(cold, 1)
