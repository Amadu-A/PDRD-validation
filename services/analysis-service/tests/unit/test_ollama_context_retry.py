# services/analysis-service/tests/unit/test_ollama_context_retry.py

"""Unit tests context-aware retry shared Ollama VLM."""

from typing import Any, ClassVar, Self

import pytest
from pdrd_analysis_service.application.ports.vision_model import (
    VisionModelError,
)
from pdrd_analysis_service.core.settings import (
    PipelineSettings,
    VisionSettings,
)
from pdrd_analysis_service.infrastructure.ollama import (
    OllamaStructuredVisionModel,
)


class FakeResponse:
    """Минимальный HTTP response для Ollama adapter tests."""

    def __init__(
        self,
        payload: dict[str, Any],
    ) -> None:
        """Сохраняет response payload."""
        self._payload = payload

    def raise_for_status(
        self,
    ) -> None:
        """Имитирует успешный HTTP status."""

    def json(
        self,
    ) -> dict[str, Any]:
        """Возвращает сохранённый JSON payload."""
        return self._payload


class FakeAsyncClient:
    """Записывает Ollama requests и возвращает prepared responses."""

    responses: ClassVar[list[FakeResponse]] = []

    requests: ClassVar[list[dict[str, Any]]] = []

    def __init__(
        self,
        *args: object,
        **kwargs: object,
    ) -> None:
        """Принимает совместимые параметры httpx.AsyncClient."""
        del args
        del kwargs

    async def __aenter__(
        self,
    ) -> Self:
        """Открывает fake async context."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        """Закрывает fake async context."""
        del exc_type
        del exc_value
        del traceback

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
    ) -> FakeResponse:
        """Возвращает следующий prepared Ollama response."""
        assert url.endswith(
            "/api/chat",
        )

        type(self).requests.append(
            json,
        )

        return type(self).responses.pop(
            0,
        )


def make_model(
    *,
    num_ctx: int,
    max_retries: int = 2,
    max_retry_num_predict: int = 14000,
) -> OllamaStructuredVisionModel:
    """Создаёт adapter без обращения к реальному GPU runtime."""
    return OllamaStructuredVisionModel(
        base_url="http://ollama:11434",
        model="qwen3-vl:8b-instruct",
        request_timeout_seconds=30,
        connect_timeout_seconds=5,
        health_timeout_seconds=5,
        num_ctx=num_ctx,
        max_retries=max_retries,
        keep_alive="0s",
        max_retry_num_predict=(max_retry_num_predict),
        gpu_coordinator=object(),  # type: ignore[arg-type]
        min_free_vram_bytes=1,
        unload_timeout_seconds=5,
        unload_poll_seconds=0.1,
    )


def install_fake_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    responses: list[dict[str, Any]],
) -> None:
    """Подменяет httpx client prepared responses."""
    FakeAsyncClient.responses = [
        FakeResponse(
            payload,
        )
        for payload in responses
    ]

    FakeAsyncClient.requests = []

    monkeypatch.setattr(
        ("pdrd_analysis_service.infrastructure.ollama.httpx.AsyncClient"),
        FakeAsyncClient,
    )


def test_analysis_vlm_default_context_is_32768() -> None:
    """Committed Analysis VLM default сохраняет 32K context."""
    assert VisionSettings().num_ctx == 32768


def test_normative_check_has_single_large_output_budget() -> None:
    """Normative check получает budget, достаточный для high-recall JSON."""
    assert VisionSettings().max_retry_num_predict == 14000

    assert PipelineSettings().norm_check_num_predict == 14000


async def test_context_exhaustion_does_not_start_impossible_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Context exhaustion не повторяется с ещё большим request."""
    install_fake_client(
        monkeypatch,
        responses=[
            {
                "message": {
                    "content": ('{"summary":"обрезано"'),
                    "thinking": "",
                },
                "done_reason": "length",
                "prompt_eval_count": 16351,
                "eval_count": 33,
                "total_duration": 1_000_000,
                "load_duration": 100_000,
            }
        ],
    )

    model = make_model(
        num_ctx=16384,
    )

    with pytest.raises(
        VisionModelError,
        match="Контекст Ollama исчерпан",
    ):
        await model._generate_json_locked(
            prompt="Большой N/T/U prompt",
            schema={
                "type": "object",
            },
            num_predict=2600,
            seed=200,
            stage="normative_check:11",
            image_bytes=b"png",
        )

    assert (
        len(
            FakeAsyncClient.requests,
        )
        == 1
    )

    options = FakeAsyncClient.requests[0]["options"]

    assert options["num_ctx"] == 16384

    assert options["num_predict"] == 2600


async def test_output_limit_still_retries_when_budget_can_grow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Output truncation повторяется только с реально большим budget."""
    install_fake_client(
        monkeypatch,
        responses=[
            {
                "message": {
                    "content": ('{"summary":"обрезано"'),
                    "thinking": "",
                },
                "done_reason": "length",
                "prompt_eval_count": 1000,
                "eval_count": 2600,
                "total_duration": 1_000_000,
                "load_duration": 100_000,
            },
            {
                "message": {
                    "content": ('{"summary":"готово","violations":[]}'),
                    "thinking": "",
                },
                "done_reason": "stop",
                "prompt_eval_count": 1040,
                "eval_count": 80,
                "total_duration": 1_000_000,
                "load_duration": 100_000,
            },
        ],
    )

    model = make_model(
        num_ctx=16384,
        max_retry_num_predict=6000,
    )

    result = await model._generate_json_locked(
        prompt="Небольшой prompt",
        schema={
            "type": "object",
        },
        num_predict=2600,
        seed=200,
        stage="normative_check:1",
        image_bytes=None,
    )

    assert result.payload == {
        "summary": "готово",
        "violations": [],
    }

    assert (
        len(
            FakeAsyncClient.requests,
        )
        == 2
    )

    first_options = FakeAsyncClient.requests[0]["options"]

    second_options = FakeAsyncClient.requests[1]["options"]

    assert first_options["num_predict"] == 2600

    assert second_options["num_predict"] == 5200

    second_prompt = FakeAsyncClient.requests[1]["messages"][0]["content"]

    assert "ПРЕДЫДУЩИЙ ОТВЕТ БЫЛ ОБРЕЗАН" in second_prompt


async def test_output_limit_does_not_repeat_same_maximum_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """14K truncation не запускает вторую идентичную 14K generation."""
    install_fake_client(
        monkeypatch,
        responses=[
            {
                "message": {
                    "content": ('{"violations":[{"comment":"обрезано"'),
                    "thinking": "",
                },
                "done_reason": "length",
                "prompt_eval_count": 13855,
                "eval_count": 14000,
                "total_duration": 1_000_000,
                "load_duration": 100_000,
            }
        ],
    )

    model = make_model(
        num_ctx=32768,
        max_retry_num_predict=14000,
    )

    with pytest.raises(
        VisionModelError,
        match=("Повтор с тем же output budget запрещён"),
    ):
        await model._generate_json_locked(
            prompt="Большой high-recall prompt",
            schema={
                "type": "object",
            },
            num_predict=14000,
            seed=200,
            stage="normative_check:1",
            image_bytes=b"png",
        )

    assert (
        len(
            FakeAsyncClient.requests,
        )
        == 1
    )

    assert FakeAsyncClient.requests[0]["options"]["num_predict"] == 14000


async def test_malformed_stop_response_can_retry_same_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Syntax failure при stop сохраняет один полезный corrective retry."""
    install_fake_client(
        monkeypatch,
        responses=[
            {
                "message": {
                    "content": ('{"violations": invalid}'),
                    "thinking": "",
                },
                "done_reason": "stop",
                "prompt_eval_count": 1200,
                "eval_count": 50,
                "total_duration": 1_000_000,
                "load_duration": 100_000,
            },
            {
                "message": {
                    "content": ('{"violations":[]}'),
                    "thinking": "",
                },
                "done_reason": "stop",
                "prompt_eval_count": 1200,
                "eval_count": 30,
                "total_duration": 1_000_000,
                "load_duration": 100_000,
            },
        ],
    )

    model = make_model(
        num_ctx=32768,
        max_retry_num_predict=14000,
    )

    result = await model._generate_json_locked(
        prompt="Prompt",
        schema={
            "type": "object",
        },
        num_predict=14000,
        seed=200,
        stage="normative_check:1",
        image_bytes=None,
    )

    assert result.payload == {
        "violations": [],
    }

    assert (
        len(
            FakeAsyncClient.requests,
        )
        == 2
    )

    assert [
        request["options"]["num_predict"] for request in FakeAsyncClient.requests
    ] == [
        14000,
        14000,
    ]
