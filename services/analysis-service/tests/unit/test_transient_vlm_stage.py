# services/analysis-service/tests/unit/test_transient_vlm_stage.py

"""Временный HTTP-отказ модели не перезапускает успешные страницы пакета."""

from collections import Counter
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pdrd_analysis_service.application.ports.vision_model import VisionModelError
from pdrd_analysis_service.core.settings import PipelineSettings
from pdrd_analysis_service.transport.http.stage_batch_routes import _run_stage_items


async def test_temporary_503_retries_only_failed_page_of_31(monkeypatch):
    """Все страницы сохраняются по порядку; успех не повторяется, число попыток ограничено."""
    counts = Counter()
    error = VisionModelError("shared-vlm HTTP 503")
    error.retryable = True
    error.status_code = 503
    error.retry_after_seconds = 0

    async def process(item):
        """Только четвёртая страница один раз встречает временный отказ."""
        counts[item] += 1
        if item == 4 and counts[item] == 1:
            raise HTTPException(503, detail=str(error)) from error
        return item * 10

    async def no_wait(*args, **kwargs):
        """Подменяет только ожидание, не выполнение страниц."""

    monkeypatch.setattr(
        "pdrd_analysis_service.transport.http.stage_batch_routes.asyncio.sleep", no_wait
    )
    container = SimpleNamespace(
        settings=SimpleNamespace(pipeline=PipelineSettings()),
        analysis_progress_probe=None,
    )
    result = await _run_stage_items(
        container=container,
        document_id=uuid4(),
        stage="checking_requirements",
        items=list(range(1, 32)),
        process=process,
    )
    assert result == [index * 10 for index in range(1, 32)]
    assert counts[4] == 2 and all(n == 1 for page, n in counts.items() if page != 4)


@pytest.mark.parametrize("code,retryable,expected", [(503, True, 3), (400, False, 1)])
async def test_persistent_or_non_retryable_error_remains_visible(
    monkeypatch, code, retryable, expected
):
    """Исчерпанный повтор и плохой запрос не выдаются за успешный пустой результат."""
    calls = 0
    error = VisionModelError("upstream", status_code=code)
    error.retryable = retryable

    async def process(item):
        """Постоянный отказ единственной страницы."""
        nonlocal calls
        calls += 1
        raise HTTPException(503, detail="upstream") from error

    async def no_wait(*args, **kwargs):
        """Исключает реальные задержки мок-сценария."""

    monkeypatch.setattr(
        "pdrd_analysis_service.transport.http.stage_batch_routes.asyncio.sleep", no_wait
    )
    container = SimpleNamespace(
        settings=SimpleNamespace(pipeline=PipelineSettings()),
        analysis_progress_probe=None,
    )
    with pytest.raises(HTTPException):
        await _run_stage_items(
            container=container,
            document_id=uuid4(),
            stage="checking_requirements",
            items=[1],
            process=process,
        )
    assert calls == expected


async def test_cancel_during_retry_does_not_make_another_model_call(monkeypatch):
    """Отмена во время backoff возвращает штатную отмену, а не 503 в n8n."""
    calls = 0

    class Probe:
        """Разрешает первую попытку и отменяет последующее ожидание."""

        async def is_cancelled(self, **kwargs):
            """После первого обращения к модели работа больше не разрешена."""
            return calls > 0

    async def process(item):
        """Временный отказ перед сохранённой отменой."""
        nonlocal calls
        calls += 1
        raise HTTPException(503) from VisionModelError("upstream", status_code=503)

    container = SimpleNamespace(
        settings=SimpleNamespace(pipeline=PipelineSettings()),
        analysis_progress_probe=Probe(),
    )
    result = await _run_stage_items(
        container=container,
        document_id=uuid4(),
        stage="checking_requirements",
        items=[1],
        process=process,
    )
    assert result is None and calls == 1
