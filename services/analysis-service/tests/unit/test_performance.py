# services/analysis-service/tests/unit/test_performance.py

"""Регрессии измерений параллельных этапов и допуска крупных запросов."""

import asyncio

import pytest
from pdrd_analysis_service.application.limited_vision import LimitedVisionModel
from pdrd_analysis_service.application.performance import (
    observe_vision_call,
    stage_profile,
    summarize_profiles,
)
from pdrd_analysis_service.domain.analysis import GenerationMetrics, GenerationResult


def test_overlapping_requests_do_not_double_stage_wall_time_or_replace_unknown_tokens():
    """Две полностью параллельные страницы дают 1 секунду wall, а не 2."""
    rows = [
        {
            "stage": "check_norms",
            "document_id": "d",
            "job_id": None,
            "page": i,
            "started_at": "2026-11-13T00:00:00+00:00",
            "ended_at": "2026-11-13T00:00:01+00:00",
            "physical_vlm_calls": 1,
            "logical_vlm_calls": 1,
            "input_tokens": None,
            "output_tokens": 5,
            "gpu_wait_ms": 20,
            "cache_hits": 0,
            "cache_misses": 1,
            "context_bytes": 100,
            "image_bytes": 10,
        }
        for i in [1, 2]
    ]
    report = summarize_profiles(rows, job_id="job")
    assert report["measured_span_ms"] == report["stages"][0]["wall_ms"] == 1000
    assert report["stages"][0]["input_tokens"] is None
    assert report["stages"][0]["output_tokens"] == 10
    assert report["job_id"] == "job" and report["document_id"] == "d"


@pytest.mark.asyncio
async def test_cache_hit_is_logical_call_without_physical_request():
    """Сводка не приписывает сохранённому ответу реальные токены и работу GPU."""

    @observe_vision_call
    async def cached(**kwargs):
        """Возвращает метрики настоящего cache-hit контракта."""
        return GenerationResult(
            {}, GenerationMetrics(0, "cache_hit", 0, 0, 0, 0, 0, 0, 0)
        )

    with stage_profile(document_id="doc", stage="understanding", page=26) as profile:
        await cached(
            prompt="Сохранённые данные", schema={}, image_bytes=b"image", stage="test"
        )
    result = profile.result()
    assert result["logical_vlm_calls"] == result["cache_hits"] == 1
    assert (
        result["physical_vlm_calls"]
        == result["input_tokens"]
        == result["output_tokens"]
        == 0
    )
    assert result["cpu_ms"] is None and result["job_id"] is None
    assert result["retries"] == result["provider_work_ms"] == 0


async def test_profile_preserves_provider_retries_and_work_separately_from_wall():
    """Три физические попытки одного обращения дают два повтора, а не три логических запроса."""

    @observe_vision_call
    async def retried(**kwargs):
        """Возвращает совмещённые метрики адаптера после временных сбоев."""
        return GenerationResult(
            {}, GenerationMetrics(3, "stop", 10, 120, 0, None, 5, 0, 0)
        )

    with stage_profile(document_id="d", stage="check_norms", page=1) as profile:
        await retried(prompt="input", schema={}, stage="test")
    result = profile.result()
    assert result["logical_vlm_calls"] == 1 and result["physical_vlm_calls"] == 3
    assert result["retries"] == 2 and result["provider_work_ms"] == 120
    assert result["input_tokens"] is None


@pytest.mark.asyncio
async def test_weighted_admission_keeps_full_prompt_and_releases_cancelled_waiter():
    """Несколько больших запросов не переполняют бюджет; отменённый не попадает провайдеру."""
    started, release = [], asyncio.Event()

    class Delegate:
        """Держит реальные coroutine в работе до контролируемого завершения."""

        async def generate_json(self, **kwargs):
            """Проверяет полную сохранность входного контекста."""
            assert kwargs["prompt"] == "данные" * 3000
            started.append(kwargs["stage"])
            await release.wait()
            return kwargs["stage"]

    model = LimitedVisionModel(Delegate(), max_concurrent=4, token_budget=20000)

    async def call(stage):
        """Передаёт крупный запрос без искусственного сокращения данных."""
        return await model.generate_json(
            prompt="данные" * 3000, schema={}, num_predict=1000, seed=1, stage=stage
        )

    first = asyncio.create_task(call("one"))
    await asyncio.sleep(0)
    second = asyncio.create_task(call("cancel"))
    third = asyncio.create_task(call("three"))
    await asyncio.sleep(0)
    assert started == ["one"]
    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second
    release.set()
    assert await first == "one" and await third == "three"
    assert started == ["one", "three"] and model._active_tokens == 0
