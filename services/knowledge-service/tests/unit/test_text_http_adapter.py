# services/knowledge-service/tests/unit/test_text_http_adapter.py

"""Unit tests text adapter over resident shared embedding."""

import pytest
from pdrd_knowledge_service.application.ports.embedding import (
    EmbeddingProviderError,
)
from pdrd_knowledge_service.application.ports.multimodal_embedding import (
    MultimodalEmbeddingProviderError,
)
from pdrd_knowledge_service.infrastructure.embedding import (
    text_http,
)
from pdrd_knowledge_service.infrastructure.embedding.text_http import (
    HttpTextEmbeddingProvider,
)


class FakeSharedEmbeddingDelegate:
    """Fake delegate без release lifecycle."""

    last_instance: "FakeSharedEmbeddingDelegate | None" = None

    def __init__(
        self,
        **kwargs: object,
    ) -> None:
        """Сохраняет constructor settings."""
        self.kwargs = kwargs

        self.calls: list[
            tuple[
                object,
                ...,
            ]
        ] = []

        self.ready = True

        FakeSharedEmbeddingDelegate.last_instance = self

    async def embed(
        self,
        inputs: tuple[
            object,
            ...,
        ],
    ) -> list[list[float]]:
        """Возвращает один vector на input."""
        self.calls.append(
            inputs,
        )

        return [
            [
                float(
                    index,
                ),
                1.0,
            ]
            for index in range(
                len(
                    inputs,
                )
            )
        ]

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает readiness."""
        return self.ready


async def test_text_batch_uses_single_shared_delegate_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Text chunks больше не создают N sequential HTTP calls."""
    monkeypatch.setattr(
        text_http,
        "HttpMultimodalEmbeddingProvider",
        FakeSharedEmbeddingDelegate,
    )

    provider = HttpTextEmbeddingProvider(
        base_url=("http://shared-embedding:8000/v1"),
        request_timeout_seconds=600.0,
        connect_timeout_seconds=30.0,
        health_timeout_seconds=10.0,
    )

    result = await provider.embed(
        (
            "chunk-1",
            "chunk-2",
            "chunk-3",
        ),
        instruction="retrieve",
    )

    assert result == [
        [
            0.0,
            1.0,
        ],
        [
            1.0,
            1.0,
        ],
        [
            2.0,
            1.0,
        ],
    ]

    delegate = FakeSharedEmbeddingDelegate.last_instance

    assert delegate is not None

    assert (
        len(
            delegate.calls,
        )
        == 1
    )

    inputs = delegate.calls[0]

    assert [item.text for item in inputs] == [
        "chunk-1",
        "chunk-2",
        "chunk-3",
    ]

    assert all((item.instruction == "retrieve") for item in inputs)

    # У fake delegate намеренно нет release().
    # Если Text provider снова попробует model unload,
    # этот test упадёт с AttributeError.
    assert not hasattr(
        delegate,
        "release",
    )


async def test_provider_error_is_mapped_to_text_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shared provider error не протекает через application port."""

    class FailingDelegate(FakeSharedEmbeddingDelegate):
        """Всегда возвращает provider error."""

        async def embed(
            self,
            inputs: tuple[
                object,
                ...,
            ],
        ) -> list[list[float]]:
            """Падает до получения vectors."""
            del inputs

            raise MultimodalEmbeddingProviderError(
                "shared embedding failed",
            )

    monkeypatch.setattr(
        text_http,
        "HttpMultimodalEmbeddingProvider",
        FailingDelegate,
    )

    provider = HttpTextEmbeddingProvider(
        base_url=("http://shared-embedding:8000/v1"),
        request_timeout_seconds=600.0,
        connect_timeout_seconds=30.0,
        health_timeout_seconds=10.0,
    )

    with pytest.raises(
        EmbeddingProviderError,
        match="shared embedding failed",
    ):
        await provider.embed(
            ("chunk",),
            instruction=None,
        )


async def test_readiness_is_delegated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Text adapter использует shared readiness."""
    monkeypatch.setattr(
        text_http,
        "HttpMultimodalEmbeddingProvider",
        FakeSharedEmbeddingDelegate,
    )

    provider = HttpTextEmbeddingProvider(
        base_url=("http://shared-embedding:8000/v1"),
        request_timeout_seconds=600.0,
        connect_timeout_seconds=30.0,
        health_timeout_seconds=10.0,
    )

    assert await provider.is_ready() is True
