# services/multimodal-embedding-service/tests/unit/test_runtime.py

"""Unit tests lightweight multimodal runtime contracts."""

import asyncio
from time import monotonic
from types import SimpleNamespace

import pytest
from pdrd_multimodal_embedding_service import (
    runtime as runtime_module,
)
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


def test_model_loader_uses_direct_low_cpu_gpu_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Большой checkpoint не должен сначала собираться в CPU RAM."""
    captured: dict[
        str,
        object,
    ] = {}

    model_dtype = object()

    fake_cuda = SimpleNamespace(
        is_available=lambda: True,
        mem_get_info=lambda: (
            24 * 1024 * 1024 * 1024,
            24 * 1024 * 1024 * 1024,
        ),
        empty_cache=lambda: None,
        ipc_collect=lambda: None,
    )

    fake_torch = SimpleNamespace(
        cuda=fake_cuda,
        bfloat16=model_dtype,
    )

    class FakeModel:
        """Минимальная fake SentenceTransformer model."""

        max_seq_length: int | None = None

    def fake_sentence_transformer(
        model_name: str,
        **kwargs: object,
    ) -> FakeModel:
        captured["model_name"] = model_name

        captured["kwargs"] = kwargs

        return FakeModel()

    fake_sentence_transformers = SimpleNamespace(
        SentenceTransformer=fake_sentence_transformer,
    )

    original_import = runtime_module.importlib.import_module

    def fake_import(
        module_name: str,
    ) -> object:
        if module_name == "torch":
            return fake_torch

        if module_name == "sentence_transformers":
            return fake_sentence_transformers

        return original_import(
            module_name,
        )

    monkeypatch.setattr(
        runtime_module.importlib,
        "import_module",
        fake_import,
    )

    runtime = Qwen3VlEmbeddingRuntime(
        settings=ModelSettings(),
    )

    model = runtime._ensure_model_sync()

    assert captured["model_name"] == ("Qwen/Qwen3-VL-Embedding-8B")

    kwargs = captured["kwargs"]

    assert isinstance(
        kwargs,
        dict,
    )

    assert "device" not in kwargs

    model_kwargs = kwargs["model_kwargs"]

    assert isinstance(
        model_kwargs,
        dict,
    )

    assert model_kwargs["dtype"] is model_dtype

    assert model_kwargs["device_map"] == "cuda:0"

    assert model_kwargs["low_cpu_mem_usage"] is True

    assert "torch_dtype" not in model_kwargs

    assert model.max_seq_length == 8192


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
