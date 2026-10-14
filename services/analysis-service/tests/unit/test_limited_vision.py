# services/analysis-service/tests/unit/test_limited_vision.py

"""Общий VLM лимит объединяет стадии, задания и адресные EQ запросы."""

import asyncio
from dataclasses import dataclass, field

import pytest
from pdrd_analysis_service.application.limited_vision import LimitedVisionModel
from pdrd_analysis_service.application.vision_execution import (
    AnalysisCancelledError,
    VisionExecution,
    vision_execution,
)


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


async def _not_cancelled() -> bool:
    """Оставляет тестовое задание активным без обращения к Gateway."""
    return False


async def _job_call(model, job, stage):
    """Передаёт настоящую идентичность задания через application-контекст."""
    with vision_execution(VisionExecution(job, _not_cancelled)):
        return await _call(model, stage)


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


async def test_job_round_robin_does_not_drain_first_job_backlog() -> None:
    """Второе задание получает слот до опустошения очереди первого, все ответы сохранены."""
    provider = BlockingVision()
    limiter = LimitedVisionModel(provider, max_concurrent=1)
    first = [asyncio.create_task(_job_call(limiter, "A", f"A-{i}")) for i in range(12)]
    await asyncio.sleep(0)
    second = [asyncio.create_task(_job_call(limiter, "B", f"B-{i}")) for i in range(3)]
    await asyncio.sleep(0)
    provider.release.set()
    results = await asyncio.wait_for(asyncio.gather(*first, *second), 1)
    assert results == [*[f"A-{i}" for i in range(12)], *[f"B-{i}" for i in range(3)]]
    assert provider.calls.index("B-0") <= 2
    assert provider.calls.index("B-2") < provider.calls.index("A-5")
    assert provider.peak == 1 and provider.active == 0


async def test_durable_cancellation_is_rechecked_after_slot_wait() -> None:
    """Отмена между постановкой в очередь и выдачей слота не достигает GPU."""
    provider = BlockingVision()
    limiter = LimitedVisionModel(provider, max_concurrent=1)
    first = asyncio.create_task(_job_call(limiter, "A", "active"))
    await asyncio.sleep(0)
    cancelled = False
    reports = []

    async def check() -> bool:
        """Имитирует уже сохранённую отмену ожидающего задания."""
        return cancelled

    async def report(waiting: int, running: int) -> bool:
        """Фиксирует раздельные состояния ожидания и генерации."""
        reports.append((waiting, running))
        return cancelled

    with vision_execution(VisionExecution("B", check, report)):
        second = asyncio.create_task(_call(limiter, "cancelled"))
    await asyncio.sleep(0)
    assert (1, 0) in reports
    cancelled = True
    provider.release.set()
    await asyncio.wait_for(first, 1)
    with pytest.raises(AnalysisCancelledError):
        await asyncio.wait_for(second, 1)
    assert provider.calls == ["active"]
    assert await _job_call(limiter, "C", "next") == "next"
