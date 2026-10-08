# services/analysis-service/tests/unit/test_limited_vision.py

"""Общий VLM лимит объединяет стадии, задания и адресные EQ запросы."""

import asyncio
from dataclasses import dataclass, field

import pytest
from pdrd_analysis_service.application.limited_vision import LimitedVisionModel


@dataclass
class BlockingVision:
    """Измеряет реальную параллельность без доступа к модели."""

    active: int = 0
    peak: int = 0
    calls: list[str] = field(default_factory=list)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def generate_json(self, **kwargs):
        """Держит слот до разрешения теста и освобождает при отмене."""
        self.calls.append(kwargs["stage"])
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await self.release.wait()
            return kwargs["stage"]
        finally:
            self.active -= 1

    async def is_ready(self):
        """Готовность не является GPU inference."""
        return True


async def _call(model, stage):
    """Передаёт одинаковый контракт для разных этапов анализа."""
    return await model.generate_json(
        prompt="test", schema={}, num_predict=1, seed=1, stage=stage
    )


@pytest.mark.asyncio
async def test_competing_stages_and_jobs_share_one_vlm_limit() -> None:
    """EQ не увеличивает число GPU операций сверх общего лимита."""
    delegate = BlockingVision()
    model = LimitedVisionModel(delegate, max_concurrent=2)
    tasks = [
        asyncio.create_task(_call(model, stage))
        for stage in ("understand:job1", "norms:job2", "equipment_document_vision:p1")
    ]
    await asyncio.sleep(0)
    assert len(delegate.calls) == 2 and delegate.peak == 2
    assert await model.is_ready()
    delegate.release.set()
    await asyncio.gather(*tasks)
    assert len(delegate.calls) == 3 and delegate.peak == 2


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_reach_provider() -> None:
    """Отмена в очереди limiter не создаёт нового GPU запроса."""
    delegate = BlockingVision()
    model = LimitedVisionModel(delegate, max_concurrent=1)
    first = asyncio.create_task(_call(model, "understand"))
    await asyncio.sleep(0)
    waiting = asyncio.create_task(_call(model, "equipment"))
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    delegate.release.set()
    await first
    assert delegate.calls == ["understand"]
    assert await _call(model, "next") == "next"


@pytest.mark.asyncio
async def test_cancelled_active_request_releases_common_slot() -> None:
    """Отмена работающей операции освобождает слот следующему заданию."""
    delegate = BlockingVision()
    model = LimitedVisionModel(delegate, max_concurrent=1)
    task = asyncio.create_task(_call(model, "cancelled"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    delegate.release.set()
    assert await _call(model, "next") == "next"
    assert delegate.peak == 1
