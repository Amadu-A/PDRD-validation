# services/knowledge-service/tests/unit/test_multimodal_http_adapter.py

"""Unit tests shared vLLM multimodal embedding adapter."""

from typing import Any

import httpx
import pytest
from pdrd_knowledge_service.application.ports.multimodal_embedding import (
    MultimodalEmbeddingInput,
    MultimodalEmbeddingProviderError,
    MultimodalEmbeddingTemporaryError,
)
from pdrd_knowledge_service.infrastructure.embedding import (
    multimodal_http,
)
from pdrd_knowledge_service.infrastructure.embedding.multimodal_http import (
    HttpMultimodalEmbeddingProvider,
)


def _provider(
    *,
    output_dimension: int = 2,
) -> HttpMultimodalEmbeddingProvider:
    """Создаёт provider для transport unit tests."""
    return HttpMultimodalEmbeddingProvider(
        base_url=("http://shared-embedding:8000/v1"),
        request_timeout_seconds=600.0,
        connect_timeout_seconds=30.0,
        health_timeout_seconds=10.0,
        model="shared-embedding",
        output_dimension=output_dimension,
    )


async def test_multiple_inputs_use_single_batched_chat_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Несколько inputs отправляются одним vLLM batch request."""
    calls: list[
        dict[
            str,
            Any,
        ]
    ] = []

    class FakeAsyncClient:
        """Фиксирует outbound request."""

        def __init__(
            self,
            *,
            timeout: object,
        ) -> None:
            """Сохраняет совместимый constructor."""
            assert timeout is not None

        async def __aenter__(
            self,
        ) -> "FakeAsyncClient":
            """Открывает fake client."""
            return self

        async def __aexit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> None:
            """Закрывает fake client."""
            del (
                exc_type,
                exc_value,
                traceback,
            )

        async def post(
            self,
            url: str,
            *,
            json: dict[
                str,
                Any,
            ],
        ) -> httpx.Response:
            """Возвращает OpenAI-compatible embedding response."""
            calls.append(
                {
                    "url": url,
                    "json": json,
                }
            )

            return httpx.Response(
                status_code=200,
                json={
                    "object": "list",
                    "model": "shared-embedding",
                    "data": [
                        {
                            "object": "embedding",
                            "index": 0,
                            "embedding": [
                                1.0,
                                10.0,
                            ],
                        },
                        {
                            "object": "embedding",
                            "index": 1,
                            "embedding": [
                                2.0,
                                20.0,
                            ],
                        },
                        {
                            "object": "embedding",
                            "index": 2,
                            "embedding": [
                                3.0,
                                30.0,
                            ],
                        },
                    ],
                },
                request=httpx.Request(
                    "POST",
                    url,
                ),
            )

    monkeypatch.setattr(
        multimodal_http.httpx,
        "AsyncClient",
        FakeAsyncClient,
    )

    result = await _provider().embed(
        (
            MultimodalEmbeddingInput(
                text="query-1",
                instruction="retrieve",
            ),
            MultimodalEmbeddingInput(
                text="query-2",
                instruction="retrieve",
            ),
            MultimodalEmbeddingInput(
                text="query-3",
                instruction="retrieve",
            ),
        )
    )

    assert result == [
        [
            1.0,
            10.0,
        ],
        [
            2.0,
            20.0,
        ],
        [
            3.0,
            30.0,
        ],
    ]

    assert (
        len(
            calls,
        )
        == 1
    )

    call = calls[0]

    assert call["url"] == ("http://shared-embedding:8000/v1/embeddings")

    payload = call["json"]

    assert payload["model"] == "shared-embedding"

    assert payload["dimensions"] == 2

    assert payload["encoding_format"] == "float"

    messages = payload["messages"]

    assert isinstance(
        messages,
        list,
    )

    assert (
        len(
            messages,
        )
        == 3
    )

    for (
        conversation,
        expected_text,
    ) in zip(
        messages,
        (
            "query-1",
            "query-2",
            "query-3",
        ),
        strict=True,
    ):
        assert conversation[0]["role"] == "system"

        assert conversation[0]["content"][0]["text"] == "retrieve"

        assert conversation[1]["content"][0]["text"] == expected_text


async def test_single_multimodal_input_uses_instruction_and_data_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T page сохраняет instruction, text и PNG image."""
    calls: list[
        dict[
            str,
            Any,
        ]
    ] = []

    class FakeAsyncClient:
        """Фиксирует one-item request."""

        def __init__(
            self,
            *,
            timeout: object,
        ) -> None:
            """Проверяет timeout."""
            assert timeout is not None

        async def __aenter__(
            self,
        ) -> "FakeAsyncClient":
            """Открывает fake client."""
            return self

        async def __aexit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> None:
            """Закрывает fake client."""
            del (
                exc_type,
                exc_value,
                traceback,
            )

        async def post(
            self,
            url: str,
            *,
            json: dict[
                str,
                Any,
            ],
        ) -> httpx.Response:
            """Возвращает one-item response."""
            calls.append(
                {
                    "url": url,
                    "json": json,
                }
            )

            return httpx.Response(
                status_code=200,
                json={
                    "object": "list",
                    "model": "shared-embedding",
                    "data": [
                        {
                            "object": "embedding",
                            "index": 0,
                            "embedding": [
                                0.25,
                                0.75,
                            ],
                        }
                    ],
                },
                request=httpx.Request(
                    "POST",
                    url,
                ),
            )

    monkeypatch.setattr(
        multimodal_http.httpx,
        "AsyncClient",
        FakeAsyncClient,
    )

    png_bytes = b"\x89PNG\r\n\x1a\ntest-image"

    result = await _provider().embed(
        (
            MultimodalEmbeddingInput(
                text=("Принципиальная схема котельной."),
                image_bytes=png_bytes,
                instruction=("Represent this drawing for retrieval."),
            ),
        )
    )

    assert result == [
        [
            0.25,
            0.75,
        ]
    ]

    assert (
        len(
            calls,
        )
        == 1
    )

    messages = calls[0]["json"]["messages"]

    assert messages[0]["role"] == "system"

    assert messages[0]["content"][0]["text"] == (
        "Represent this drawing for retrieval."
    )

    user_content = messages[1]["content"]

    assert user_content[0]["type"] == "image_url"

    assert user_content[0]["image_url"]["url"].startswith("data:image/png;base64,")

    assert user_content[1] == {
        "type": "text",
        "text": ("Принципиальная схема котельной."),
    }


async def test_default_instruction_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Документ без custom prompt получает default Qwen instruction."""
    payloads: list[
        dict[
            str,
            Any,
        ]
    ] = []

    class FakeAsyncClient:
        """Возвращает successful embedding."""

        def __init__(
            self,
            *,
            timeout: object,
        ) -> None:
            """Проверяет timeout."""
            assert timeout is not None

        async def __aenter__(
            self,
        ) -> "FakeAsyncClient":
            """Открывает client."""
            return self

        async def __aexit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> None:
            """Закрывает client."""
            del (
                exc_type,
                exc_value,
                traceback,
            )

        async def post(
            self,
            url: str,
            *,
            json: dict[
                str,
                Any,
            ],
        ) -> httpx.Response:
            """Фиксирует payload."""
            payloads.append(
                json,
            )

            return httpx.Response(
                status_code=200,
                json={
                    "model": "shared-embedding",
                    "data": [
                        {
                            "index": 0,
                            "embedding": [
                                1.0,
                                2.0,
                            ],
                        }
                    ],
                },
                request=httpx.Request(
                    "POST",
                    url,
                ),
            )

    monkeypatch.setattr(
        multimodal_http.httpx,
        "AsyncClient",
        FakeAsyncClient,
    )

    await _provider().embed(
        (
            MultimodalEmbeddingInput(
                text="СП 60.13330.2020",
            ),
        )
    )

    messages = payloads[0]["messages"]

    assert messages[0]["content"][0]["text"] == "Represent the user's input."


async def test_temporary_http_failure_is_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transient shared-vLLM error остаётся retryable."""
    calls = 0

    class FakeAsyncClient:
        """Имитирует shared backpressure."""

        def __init__(
            self,
            *,
            timeout: object,
        ) -> None:
            """Проверяет timeout."""
            assert timeout is not None

        async def __aenter__(
            self,
        ) -> "FakeAsyncClient":
            """Открывает fake client."""
            return self

        async def __aexit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> None:
            """Закрывает fake client."""
            del (
                exc_type,
                exc_value,
                traceback,
            )

        async def post(
            self,
            url: str,
            *,
            json: dict[
                str,
                Any,
            ],
        ) -> httpx.Response:
            """Возвращает 503."""
            nonlocal calls

            del json

            calls += 1

            return httpx.Response(
                status_code=503,
                text=("temporary shared embedding backpressure"),
                request=httpx.Request(
                    "POST",
                    url,
                ),
            )

    monkeypatch.setattr(
        multimodal_http.httpx,
        "AsyncClient",
        FakeAsyncClient,
    )

    with pytest.raises(
        MultimodalEmbeddingTemporaryError,
        match="503",
    ):
        await _provider().embed(
            (
                MultimodalEmbeddingInput(
                    text="query-1",
                ),
                MultimodalEmbeddingInput(
                    text="query-2",
                ),
            )
        )

    assert calls == 1


async def test_wrong_dimension_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adapter не допускает silent vector-space mismatch."""

    class FakeAsyncClient:
        """Возвращает vector неверной dimension."""

        def __init__(
            self,
            *,
            timeout: object,
        ) -> None:
            """Проверяет timeout."""
            assert timeout is not None

        async def __aenter__(
            self,
        ) -> "FakeAsyncClient":
            """Открывает fake client."""
            return self

        async def __aexit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> None:
            """Закрывает fake client."""
            del (
                exc_type,
                exc_value,
                traceback,
            )

        async def post(
            self,
            url: str,
            *,
            json: dict[
                str,
                Any,
            ],
        ) -> httpx.Response:
            """Возвращает bad dimension."""
            del json

            return httpx.Response(
                status_code=200,
                json={
                    "model": "shared-embedding",
                    "data": [
                        {
                            "index": 0,
                            "embedding": [
                                1.0,
                            ],
                        }
                    ],
                },
                request=httpx.Request(
                    "POST",
                    url,
                ),
            )

    monkeypatch.setattr(
        multimodal_http.httpx,
        "AsyncClient",
        FakeAsyncClient,
    )

    with pytest.raises(
        MultimodalEmbeddingProviderError,
        match="dimension",
    ):
        await _provider().embed(
            (
                MultimodalEmbeddingInput(
                    text="query",
                ),
            )
        )


async def test_is_ready_requires_shared_model_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Readiness проверяет и health, и logical alias."""
    urls: list[str] = []

    class FakeAsyncClient:
        """Fake health/models client."""

        def __init__(
            self,
            *,
            timeout: object,
        ) -> None:
            """Проверяет timeout."""
            assert timeout is not None

        async def __aenter__(
            self,
        ) -> "FakeAsyncClient":
            """Открывает client."""
            return self

        async def __aexit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> None:
            """Закрывает client."""
            del (
                exc_type,
                exc_value,
                traceback,
            )

        async def get(
            self,
            url: str,
        ) -> httpx.Response:
            """Отвечает на health/models."""
            urls.append(
                url,
            )

            if url.endswith(
                "/health",
            ):
                return httpx.Response(
                    status_code=200,
                    request=httpx.Request(
                        "GET",
                        url,
                    ),
                )

            return httpx.Response(
                status_code=200,
                json={
                    "object": "list",
                    "data": [
                        {
                            "id": ("shared-embedding"),
                        }
                    ],
                },
                request=httpx.Request(
                    "GET",
                    url,
                ),
            )

    monkeypatch.setattr(
        multimodal_http.httpx,
        "AsyncClient",
        FakeAsyncClient,
    )

    assert await _provider().is_ready() is True

    assert urls == [
        ("http://shared-embedding:8000/health"),
        ("http://shared-embedding:8000/v1/models"),
    ]


async def test_release_is_resident_compatibility_noop() -> None:
    """Shared checkpoint не имеет project-owned release lifecycle."""
    provider = _provider()

    assert await provider.release() is None
