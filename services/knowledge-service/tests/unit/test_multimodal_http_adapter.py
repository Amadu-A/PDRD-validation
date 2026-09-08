# services/knowledge-service/tests/unit/test_multimodal_http_adapter.py

"""Unit tests bounded HTTP multimodal embedding adapter."""

from typing import Any

import httpx
import pytest
from pdrd_knowledge_service.application.ports.multimodal_embedding import (
    MultimodalEmbeddingInput,
    MultimodalEmbeddingTemporaryError,
)
from pdrd_knowledge_service.infrastructure.embedding import (
    multimodal_http,
)
from pdrd_knowledge_service.infrastructure.embedding.multimodal_http import (
    HttpMultimodalEmbeddingProvider,
)


def _provider() -> HttpMultimodalEmbeddingProvider:
    """Создаёт provider для transport unit tests."""
    return HttpMultimodalEmbeddingProvider(
        base_url="http://multimodal:8601",
        request_timeout_seconds=1800.0,
        connect_timeout_seconds=30.0,
        health_timeout_seconds=10.0,
    )


async def test_multi_input_is_serialized_into_single_item_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adapter соблюдает service max_batch_size=1 для нескольких inputs."""
    calls: list[
        dict[
            str,
            Any,
        ]
    ] = []

    class FakeAsyncClient:
        """Фиксирует outbound bounded requests."""

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
            del exc_type
            del exc_value
            del traceback

        async def post(
            self,
            url: str,
            *,
            json: dict[
                str,
                Any,
            ],
        ) -> httpx.Response:
            """Возвращает отдельный vector для каждого вызова."""
            calls.append(
                {
                    "url": url,
                    "json": json,
                }
            )

            call_number = len(
                calls,
            )

            return httpx.Response(
                status_code=200,
                json={
                    "embeddings": [
                        [
                            float(
                                call_number,
                            ),
                            float(
                                call_number * 10,
                            ),
                        ]
                    ]
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
        == 3
    )

    assert [len(call["json"]["inputs"]) for call in calls] == [
        1,
        1,
        1,
    ]

    assert [call["json"]["inputs"][0]["text"] for call in calls] == [
        "query-1",
        "query-2",
        "query-3",
    ]

    assert all(
        call["url"].endswith(
            "/internal/v1/embeddings",
        )
        for call in calls
    )


async def test_temporary_failure_stops_remaining_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Temporary provider error не запускает оставшуюся очередь inputs."""
    calls: list[str] = []

    class FakeAsyncClient:
        """Имитирует temporary failure второго request."""

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
            del exc_type
            del exc_value
            del traceback

        async def post(
            self,
            url: str,
            *,
            json: dict[
                str,
                Any,
            ],
        ) -> httpx.Response:
            """Первый request успешен, второй временно недоступен."""
            text = str(json["inputs"][0]["text"])

            calls.append(
                text,
            )

            if (
                len(
                    calls,
                )
                == 2
            ):
                return httpx.Response(
                    status_code=503,
                    text="temporary GPU admission failure",
                    request=httpx.Request(
                        "POST",
                        url,
                    ),
                )

            return httpx.Response(
                status_code=200,
                json={
                    "embeddings": [
                        [
                            1.0,
                            2.0,
                        ]
                    ]
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
                MultimodalEmbeddingInput(
                    text="query-3",
                ),
            )
        )

    assert calls == [
        "query-1",
        "query-2",
    ]
