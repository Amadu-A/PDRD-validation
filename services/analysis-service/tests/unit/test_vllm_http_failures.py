# services/analysis-service/tests/unit/test_vllm_http_failures.py

"""Коды отказов shared-vlm и Retry-After без раскрытия исходных документов."""

import httpx
import pytest
from pdrd_analysis_service.application.ports.vision_model import VisionModelError
from pdrd_analysis_service.infrastructure.vllm import VllmStructuredVisionModel

from .test_vllm_adapter import (
    FakeAsyncClient,
    _completion,
    _install_fake_client,
    _model,
)


@pytest.mark.parametrize(
    "status,code,retryable",
    [(503, "engine_unavailable", True), (400, "context_length_exceeded", False)],
)
async def test_provider_failure_carries_safe_code_and_retry_after(
    monkeypatch, status, code, retryable
):
    """Ошибка остаётся различимой, но тело с проектным текстом не попадает в сообщение."""
    _install_fake_client(monkeypatch)
    FakeAsyncClient.post_responses = [
        httpx.Response(
            status,
            request=httpx.Request("POST", "http://test"),
            json={"error": {"code": code, "message": "СЕКРЕТНЫЙ ТЕКСТ ПРОЕКТА"}},
            headers={"Retry-After": "5", "x-request-id": "req-1"},
        )
    ]
    with pytest.raises(VisionModelError) as failure:
        await _model(max_attempts=1).generate_json(
            prompt="Проект",
            schema={"type": "object"},
            num_predict=100,
            seed=1,
            stage="normative_check:4:probe1",
        )
    error = failure.value
    assert (
        error.status_code,
        error.error_code,
        error.retryable,
        error.retry_after_seconds,
    ) == (status, code, retryable, 5)
    assert "СЕКРЕТНЫЙ" not in str(error)
    assert error.metrics.attempt == 1 and error.metrics.prompt_eval_count is None


async def test_retry_after_is_honoured_and_failed_attempt_is_counted(monkeypatch):
    """Успех после временного отказа учитывает обе попытки, без реального ожидания теста."""
    _install_fake_client(monkeypatch, post_responses=[_completion(content="{}")])
    success = FakeAsyncClient.post_responses[0]
    FakeAsyncClient.post_responses = [
        httpx.Response(
            503,
            request=httpx.Request("POST", "http://test"),
            json={"error": {"code": "busy"}},
            headers={"Retry-After": "3"},
        ),
        success,
    ]
    waited = []

    async def sleep(seconds):
        """Фиксирует backoff, не замедляя мок-сценарий."""
        waited.append(seconds)

    monkeypatch.setattr(
        "pdrd_analysis_service.infrastructure.vllm.asyncio.sleep", sleep
    )
    result = await _model().generate_json(
        prompt="Проект",
        schema={"type": "object"},
        num_predict=100,
        seed=1,
        stage="check",
    )
    assert sum(waited) == 3 and result.metrics.attempt == 2
    assert result.metrics.prompt_eval_count is None


def test_provider_message_is_not_used_as_unbounded_error_code():
    """HTML proxy и сообщения с цитатами не подменяют безопасные метаданные."""
    for response in [
        httpx.Response(503, text="<html>proxy</html>"),
        httpx.Response(503, json={"error": {"code": "текст проекта"}}),
    ]:
        code, wait, identity = VllmStructuredVisionModel._provider_failure(response)
        assert code == "" and wait == 0 and identity == ""
