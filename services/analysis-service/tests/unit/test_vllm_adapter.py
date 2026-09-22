# services/analysis-service/tests/unit/test_vllm_adapter.py

"""Unit tests OpenAI-compatible shared-vlm adapter."""

from typing import (
    Any,
    ClassVar,
    Self,
)

import pytest
from pdrd_analysis_service.infrastructure.vllm import (
    VllmStructuredVisionModel,
)


class FakeResponse:
    """Минимальный successful HTTP response."""

    def __init__(
        self,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Сохраняет response payload."""
        self._payload = payload if payload is not None else {}

    def raise_for_status(
        self,
    ) -> None:
        """Имитирует успешный status."""

    def json(
        self,
    ) -> dict[str, Any]:
        """Возвращает prepared JSON."""
        return self._payload


class FakeAsyncClient:
    """Записывает vLLM requests и отдаёт prepared responses."""

    post_responses: ClassVar[list[FakeResponse]] = []

    get_responses: ClassVar[list[FakeResponse]] = []

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
        """Открывает fake context."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        """Закрывает fake context."""
        del exc_type
        del exc_value
        del traceback

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
    ) -> FakeResponse:
        """Возвращает следующий chat completion."""
        type(self).requests.append(
            {
                "method": "POST",
                "url": url,
                "json": json,
            }
        )

        return type(self).post_responses.pop(
            0,
        )

    async def get(
        self,
        url: str,
    ) -> FakeResponse:
        """Возвращает следующий readiness response."""
        type(self).requests.append(
            {
                "method": "GET",
                "url": url,
            }
        )

        return type(self).get_responses.pop(
            0,
        )


def _model(
    *,
    max_attempts: int = 2,
    max_retry_num_predict: int = 14000,
) -> VllmStructuredVisionModel:
    """Создаёт shared-vlm adapter."""
    return VllmStructuredVisionModel(
        base_url="http://shared-vlm:8000/v1",
        model="shared-vlm",
        request_timeout_seconds=30,
        connect_timeout_seconds=5,
        health_timeout_seconds=5,
        max_attempts=max_attempts,
        retry_backoff_seconds=0,
        max_retry_num_predict=(max_retry_num_predict),
    )


def _completion(
    *,
    content: str,
    finish_reason: str = "stop",
    prompt_tokens: int = 100,
    completion_tokens: int = 20,
) -> dict[str, Any]:
    """Строит OpenAI-compatible completion."""
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content,
                    "reasoning": None,
                },
                "finish_reason": (finish_reason),
            }
        ],
        "usage": {
            "prompt_tokens": (prompt_tokens),
            "completion_tokens": (completion_tokens),
        },
    }


def _install_fake_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    post_responses: (list[dict[str, Any]] | None) = None,
    get_responses: (list[dict[str, Any]] | None) = None,
) -> None:
    """Подменяет httpx.AsyncClient."""
    FakeAsyncClient.post_responses = [
        FakeResponse(
            payload,
        )
        for payload in (post_responses or [])
    ]

    FakeAsyncClient.get_responses = [
        FakeResponse(
            payload,
        )
        for payload in (get_responses or [])
    ]

    FakeAsyncClient.requests = []

    monkeypatch.setattr(
        ("pdrd_analysis_service.infrastructure.vllm.httpx.AsyncClient"),
        FakeAsyncClient,
    )


async def test_generate_json_uses_openai_compatible_multimodal_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adapter использует logical alias, JSON Schema и image_url."""
    _install_fake_client(
        monkeypatch,
        post_responses=[
            _completion(
                content=('{"status":"ok"}'),
                prompt_tokens=89,
                completion_tokens=6,
            )
        ],
    )

    model = _model()

    result = await model.generate_json(
        prompt="Проверка.",
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "status": {
                    "type": "string",
                },
            },
            "required": [
                "status",
            ],
        },
        num_predict=64,
        seed=700,
        stage="finding_localization:23",
        image_bytes=(b"\x89PNG\r\n\x1a\nfake"),
    )

    assert result.payload == {
        "status": "ok",
    }

    assert result.metrics.prompt_eval_count == 89

    assert result.metrics.eval_count == 6

    assert result.metrics.load_duration_ms == 0.0

    assert (
        len(
            FakeAsyncClient.requests,
        )
        == 1
    )

    request = FakeAsyncClient.requests[0]

    assert request["url"] == ("http://shared-vlm:8000/v1/chat/completions")

    payload = request["json"]

    assert payload["model"] == "shared-vlm"

    assert payload["max_tokens"] == 64

    assert payload["stream"] is False

    assert payload["chat_template_kwargs"] == {
        "enable_thinking": False,
    }

    assert payload["response_format"]["type"] == "json_schema"

    json_schema = payload["response_format"]["json_schema"]

    assert json_schema["strict"] is True

    assert json_schema["name"] == ("pdrd_finding_localization_23")

    content = payload["messages"][0]["content"]

    assert isinstance(
        content,
        list,
    )

    assert content[0] == {
        "type": "text",
        "text": "Проверка.",
    }

    image_url = content[1]["image_url"]["url"]

    assert image_url.startswith(
        "data:image/png;base64,",
    )


async def test_output_limit_retries_with_larger_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Truncated completion получает один bounded retry с большим budget."""
    _install_fake_client(
        monkeypatch,
        post_responses=[
            _completion(
                content='{"findings":[',
                finish_reason="length",
                completion_tokens=4000,
            ),
            _completion(
                content='{"findings":[]}',
                completion_tokens=20,
            ),
        ],
    )

    model = _model(
        max_retry_num_predict=10000,
    )

    result = await model.generate_json(
        prompt="Prompt",
        schema={
            "type": "object",
        },
        num_predict=4000,
        seed=100,
        stage="finalize:1",
        image_bytes=None,
    )

    assert result.payload == {
        "findings": [],
    }

    assert [
        request["json"]["max_tokens"] for request in (FakeAsyncClient.requests)
    ] == [
        4000,
        8000,
    ]

    retry_content = FakeAsyncClient.requests[1]["json"]["messages"][0]["content"]

    assert "ПРЕДЫДУЩИЙ ОТВЕТ БЫЛ ОБРЕЗАН" in retry_content


async def test_readiness_checks_health_and_logical_model_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Readiness не зависит от physical Hugging Face model ID."""
    _install_fake_client(
        monkeypatch,
        get_responses=[
            {},
            {
                "object": "list",
                "data": [
                    {
                        "id": "shared-vlm",
                        "root": ("some/physical-model"),
                    }
                ],
            },
        ],
    )

    model = _model()

    assert await model.is_ready() is True

    assert [
        (
            request["method"],
            request["url"],
        )
        for request in (FakeAsyncClient.requests)
    ] == [
        (
            "GET",
            "http://shared-vlm:8000/health",
        ),
        (
            "GET",
            ("http://shared-vlm:8000/v1/models"),
        ),
    ]


async def test_readiness_rejects_missing_logical_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Другой physical model не считается configured shared-vlm."""
    _install_fake_client(
        monkeypatch,
        get_responses=[
            {},
            {
                "object": "list",
                "data": [
                    {
                        "id": "other-model",
                    }
                ],
            },
        ],
    )

    assert await _model().is_ready() is False
