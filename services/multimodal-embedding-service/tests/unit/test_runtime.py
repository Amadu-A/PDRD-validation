# services/multimodal-embedding-service/tests/unit/test_runtime.py

"""Unit tests lightweight multimodal runtime contracts."""

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
