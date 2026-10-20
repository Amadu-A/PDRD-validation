# services/knowledge-service/tests/unit/test_embedding_resilience.py

"""Управляемый retry, дедупликация и отмена без обращения к GPU."""

import asyncio
import time
from contextlib import asynccontextmanager

import httpx
import pytest
from pdrd_knowledge_service.application.embedding_execution import (
    EmbeddingCancelledError,
    EmbeddingExecution,
    embedding_execution,
)
from pdrd_knowledge_service.application.ports.embedding import EmbeddingTemporaryError
from pdrd_knowledge_service.application.ports.multimodal_embedding import (
    MultimodalEmbeddingInput,
    MultimodalEmbeddingProviderError,
)
from pdrd_knowledge_service.infrastructure.embedding.multimodal_http import (
    HttpMultimodalEmbeddingProvider,
)
from pdrd_knowledge_service.infrastructure.embedding.request_runtime import (
    EmbeddingRequestPolicy,
    EmbeddingRequestRuntime,
)


def _runtime(**options) -> EmbeddingRequestRuntime:
    """Сокращает только интервалы мок-теста, сохраняя production-алгоритм."""
    return EmbeddingRequestRuntime(
        EmbeddingRequestPolicy(
            backoff_seconds=0.001, max_backoff_seconds=0.002, **options
        )
    )


def _inputs(*texts: str) -> tuple[MultimodalEmbeddingInput, ...]:
    """Готовит исходные вопросы без заранее заданных замечаний."""
    return tuple(MultimodalEmbeddingInput(text=text) for text in texts)


@pytest.mark.parametrize("failures", [1, 3])
async def test_temporary_failures_recover_without_resending_successful_batches(
    failures: int,
) -> None:
    """Успешная первая микропорция не повторяется при сбое второй."""
    runtime = _runtime(max_batch_size=1)
    calls = []
    context = EmbeddingExecution("job-1", "requirements")

    async def send(batch, remaining):
        """Временно отклоняет только второй вопрос."""
        assert remaining > 0
        text = batch[0].text
        calls.append(text)
        if text == "второй" and calls.count(text) <= failures:
            raise EmbeddingTemporaryError("busy", status_code=503)
        return [[float(len(text))]]

    with embedding_execution(context):
        result = await runtime.execute(
            _inputs("первый", "второй", "первый"), identity="model", send=send
        )
    assert result == [[6.0], [6.0], [6.0]]
    assert calls.count("первый") == 1
    assert calls.count("второй") == failures + 1
    assert context.metrics["retries"] == failures


async def test_permanent_busy_is_bounded_and_does_not_return_empty_vectors() -> None:
    """Исчерпание попыток диагностируется, а не выдаётся за отсутствие источников."""
    runtime = _runtime(max_attempts=3)
    calls = 0

    async def send(batch, remaining):
        """Всегда возвращает перегрузку общей модели."""
        nonlocal calls
        calls += 1
        raise EmbeddingTemporaryError("busy", status_code=503)

    with pytest.raises(EmbeddingTemporaryError) as caught:
        await runtime.execute(_inputs("вопрос"), identity="model", send=send)
    assert caught.value.reason_code == "embedding_budget_exhausted"
    assert calls == 3


async def test_job_cache_reuses_exact_queries_preserving_all_positions_and_model_identity() -> (
    None
):
    """Разные требования, инструкции, модели и задания не сливаются по близости текста."""
    runtime = _runtime()
    calls = []

    async def send(batch, remaining):
        """Возвращает различимые векторы для проверяемых свойств."""
        calls.append(tuple(item.text for item in batch))
        return [[float("напор" in item.text)] for item in batch]

    with embedding_execution(EmbeddingExecution("job-1", "requirements")):
        first = await runtime.execute(
            _inputs("расход 10 л/с", "напор 10 м"), identity="m", send=send
        )
        second = await runtime.execute(
            _inputs("расход  10 л/с", "расход 10 л/с"), identity="m", send=send
        )
        assert first == [[0.0], [1.0]]
        assert second == [[0.0], [0.0]]
        assert len(calls) == 1
        await runtime.execute(
            _inputs("расход 10 л/с"), identity="different-model", send=send
        )
    with embedding_execution(EmbeddingExecution("job-2", "requirements")):
        await runtime.execute(_inputs("расход 10 л/с"), identity="m", send=send)
    assert len(calls) == 3


async def test_retry_after_releases_shared_slot_and_other_job_can_advance() -> None:
    """Ожидающий retry анализ не блокирует второй анализ своим admission-слотом."""
    occupied = 0
    sequence = []
    failed = asyncio.Event()

    @asynccontextmanager
    async def slot():
        """Проверяет отсутствие удержанного слота во время backoff."""
        nonlocal occupied
        occupied += 1
        assert occupied == 1
        try:
            yield True
        finally:
            occupied -= 1

    runtime = _runtime()
    runtime.shared_slot = slot

    async def send(batch, remaining):
        """Первое задание получает Retry-After, второе может завершиться раньше."""
        text = batch[0].text
        sequence.append(text)
        if text == "A" and sequence.count("A") == 1:
            failed.set()
            raise EmbeddingTemporaryError(
                "rate limit", status_code=429, retry_after=0.04
            )
        return [[1.0]]

    async def job(text):
        """Изолирует кэш и счётчики задания."""
        with embedding_execution(EmbeddingExecution(text, "requirements")):
            return await runtime.execute(_inputs(text), identity="m", send=send)

    start = time.monotonic()
    first = asyncio.create_task(job("A"))
    await failed.wait()
    await job("B")
    await first
    assert sequence == ["A", "B", "A"]
    assert time.monotonic() - start >= 0.04
    assert occupied == 0


async def test_cancellation_during_wait_prevents_second_request() -> None:
    """Durable отмена прерывает Retry-After без обращения к модели."""
    context = EmbeddingExecution("job", "requirements")
    calls = 0

    async def control():
        """Возвращает актуальное состояние владельца задания."""
        return {"job_id": "job", "cancelled": calls > 0, "remaining_seconds": 60}

    context.check_control = control

    async def send(batch, remaining):
        """После первого отказа инициируется отмена на Gateway."""
        nonlocal calls
        calls += 1
        raise EmbeddingTemporaryError("busy", status_code=503, retry_after=10)

    with embedding_execution(context), pytest.raises(EmbeddingCancelledError):
        await _runtime().execute(_inputs("вопрос"), identity="m", send=send)
    assert calls == 1


async def test_lifecycle_deadline_is_stricter_than_retry_after() -> None:
    """Длительное ожидание не выходит за оставшийся бюджет задания."""

    async def send(batch, remaining):
        """Сервер предлагает ждать дольше допустимого lifecycle."""
        raise EmbeddingTemporaryError("busy", retry_after=60)

    context = EmbeddingExecution(
        "job", "requirements", deadline=time.monotonic() + 0.02
    )
    with embedding_execution(context), pytest.raises(EmbeddingTemporaryError) as caught:
        await _runtime().execute(_inputs("вопрос"), identity="m", send=send)
    assert caught.value.reason_code == "analysis_deadline_exceeded"
    assert context.metrics["physical_requests"] == 1


@pytest.mark.parametrize("status", [400, 422])
async def test_invalid_request_is_never_retried(monkeypatch, status: int) -> None:
    """Постоянные HTTP-ошибки не усиливают нагрузку и не раскрывают ответ с исходным текстом."""
    calls = 0

    def respond(request):
        """Имитирует сервер валидации."""
        nonlocal calls
        calls += 1
        return httpx.Response(status, text="confidential input echoed", request=request)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    provider = HttpMultimodalEmbeddingProvider(
        base_url="http://model/v1",
        request_timeout_seconds=2,
        connect_timeout_seconds=1,
        health_timeout_seconds=1,
        runtime=_runtime(),
    )
    with pytest.raises(MultimodalEmbeddingProviderError) as caught:
        await provider.embed(_inputs("секретный документ"))
    assert calls == 1
    assert "confidential" not in str(caught.value)


async def test_timeout_has_finite_retries() -> None:
    """Таймаут учитывается отдельно от HTTP 503 и имеет конечный бюджет."""
    context = EmbeddingExecution("job", "requirements")

    async def send(batch, remaining):
        """Имитирует исчерпание сетевого timeout."""
        raise TimeoutError

    with embedding_execution(context), pytest.raises(EmbeddingTemporaryError):
        await _runtime(max_attempts=2).execute(
            _inputs("вопрос"), identity="m", send=send
        )
    assert context.metrics["timeouts"] == 2
    assert context.metrics["retries"] == 1


async def test_queue_is_bounded_and_waiting_jobs_keep_fifo_order() -> None:
    """При занятом слоте новые задачи не раздувают очередь и не вытесняют ожидающую страницу."""
    runtime = _runtime(max_waiters=1, max_attempts=1)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def send(batch, remaining):
        """Удерживает первый запрос, позволяя проверить очередь без нагрузки."""
        calls.append(batch[0].text)
        if batch[0].text == "A":
            entered.set()
            await release.wait()
        return [[1.0]]

    first = asyncio.create_task(runtime.execute(_inputs("A"), identity="m", send=send))
    await entered.wait()
    second = asyncio.create_task(runtime.execute(_inputs("B"), identity="m", send=send))
    await asyncio.sleep(0.001)
    with pytest.raises(EmbeddingTemporaryError):
        await runtime.execute(_inputs("C"), identity="m", send=send)
    release.set()
    await asyncio.gather(first, second)
    assert calls == ["A", "B"]


async def test_expired_waiter_keeps_wait_metric_and_does_not_request_model() -> None:
    """Истёкший lifecycle во время FIFO не теряет ожидание в диагностике и не запускает inference."""
    runtime = _runtime()
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def send(batch, remaining):
        """Имитирует длительный первый запрос без нагрузки."""
        calls.append(batch[0].text)
        if batch[0].text == "first":
            entered.set()
            await release.wait()
        return [[1.0]]

    first = asyncio.create_task(
        runtime.execute(_inputs("first"), identity="m", send=send)
    )
    await entered.wait()
    context = EmbeddingExecution(
        "second", "requirements", deadline=time.monotonic() + 0.02
    )
    try:
        with (
            embedding_execution(context),
            pytest.raises(EmbeddingTemporaryError) as caught,
        ):
            await runtime.execute(_inputs("second"), identity="m", send=send)
        assert caught.value.reason_code == "analysis_deadline_exceeded"
        assert (
            context.metrics["physical_requests"] == 0
            and context.metrics["wait_ms"] >= 10
        )
        assert calls == ["first"]
    finally:
        release.set()
        await first


@pytest.mark.parametrize("status", [429, 503])
async def test_http_adapter_retries_only_failed_physical_request(monkeypatch, status):
    """Проверяет реальный разбор HTTP, счётчики и Retry-After, а не только подставленное исключение."""
    calls = 0

    def respond(request):
        """После двух отказов возвращает валидный OpenAI embedding."""
        nonlocal calls
        calls += 1
        if calls <= 2:
            return httpx.Response(
                status,
                text="The engine is currently busy and cannot accept new requests",
                headers={"Retry-After": "0.01"},
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "model": "shared-embedding",
                "data": [{"index": 0, "embedding": [1.0, 2.0]}],
                "usage": {"prompt_tokens": 12},
            },
            request=request,
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    provider = HttpMultimodalEmbeddingProvider(
        base_url="http://model/v1",
        request_timeout_seconds=2,
        connect_timeout_seconds=1,
        health_timeout_seconds=1,
        output_dimension=2,
        runtime=_runtime(),
    )
    context = EmbeddingExecution("job", "requirements")
    start = time.monotonic()
    with embedding_execution(context):
        assert await provider.embed(_inputs("вопрос")) == [[1.0, 2.0]]
    assert time.monotonic() - start >= 0.02
    assert (
        context.metrics[f"http_{status}"] == 2
        and context.metrics["physical_requests"] == 3
    )
    assert context.metrics["prompt_tokens"] == 12 and calls == 3
