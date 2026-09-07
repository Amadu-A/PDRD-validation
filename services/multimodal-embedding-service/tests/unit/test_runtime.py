# services/multimodal-embedding-service/tests/unit/test_runtime.py

"""Unit tests lightweight multimodal runtime contracts."""

import asyncio
from time import monotonic

import pytest
from pdrd_multimodal_embedding_service.runtime import (
    Qwen3VlEmbeddingRuntime,
    RuntimeEmbeddingInput,
)
from pdrd_multimodal_embedding_service.settings import (
    ModelSettings,
)


async def test_empty_embedding_batch_does_not_load_model() -> None:
    """Пустой batch возвращается без CUDA/model imports."""
    runtime = Qwen3VlEmbeddingRuntime(
        settings=ModelSettings(),
    )

    result = await runtime.embed(
        (),
    )

    assert result == []


def test_runtime_input_keeps_mixed_modalities() -> None:
    """Input поддерживает text + image одновременно."""
    item = RuntimeEmbeddingInput(
        text="Шкаф управления IP54.",
        image_bytes=b"png",
        instruction="Retrieve project requirements.",
    )

    assert item.text == "Шкаф управления IP54."

    assert item.image_bytes == b"png"

    assert item.instruction is not None


@pytest.mark.asyncio
async def test_idle_watchdog_releases_loaded_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fallback автоматически освобождает idle checkpoint."""
    runtime = Qwen3VlEmbeddingRuntime(
        settings=ModelSettings(
            idle_release_seconds=0.01,
        ),
    )

    runtime._model = object()

    runtime._last_activity_at = monotonic()

    released: list[bool] = []

    def fake_release_sync() -> None:
        runtime._model = None

        released.append(
            True,
        )

    monkeypatch.setattr(
        runtime,
        "_release_sync",
        fake_release_sync,
    )

    runtime._ensure_idle_release_watchdog()

    await asyncio.sleep(
        0.05,
    )

    assert released == [
        True,
    ]

    assert runtime._model is None

    assert runtime._idle_release_task is None
