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
    GpuAdmissionError,
    Qwen3VlEmbeddingRuntime,
    RuntimeEmbeddingInput,
    SystemRamAdmissionError,
)
from pdrd_multimodal_embedding_service.settings import (
    ModelSettings,
)

GIB = 1024**3


class FakeGpuLease:
    """In-memory lease для unit-тестов model runtime."""

    def __init__(
        self,
    ) -> None:
        """Создаёт свободный fake lease."""
        self.acquired = False

        self.acquire_calls = 0

        self.release_calls = 0

    def acquire(
        self,
        *,
        timeout_seconds: float,
    ) -> None:
        """Фиксирует acquire без обращения к OS file lock."""
        assert timeout_seconds > 0

        self.acquire_calls += 1

        self.acquired = True

    def release(
        self,
    ) -> None:
        """Фиксирует release."""
        if self.acquired:
            self.release_calls += 1

        self.acquired = False


@pytest.fixture
def gpu_lease() -> FakeGpuLease:
    """Возвращает независимый fake lease каждому unit-test."""
    return FakeGpuLease()


async def test_empty_embedding_batch_does_not_load_model(
    gpu_lease: FakeGpuLease,
) -> None:
    """Пустой batch возвращается без CUDA/model imports."""
    runtime = Qwen3VlEmbeddingRuntime(
        settings=ModelSettings(),
        gpu_lease=gpu_lease,
    )

    result = await runtime.embed(
        (),
    )

    assert result == []

    assert gpu_lease.acquire_calls == 0

    assert gpu_lease.release_calls == 0


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


def test_model_loader_rejects_low_system_ram_before_imports(
    monkeypatch: pytest.MonkeyPatch,
    gpu_lease: FakeGpuLease,
) -> None:
    """RAM guard выполняется до CUDA/model imports."""
    imported_modules: list[str] = []

    monkeypatch.setattr(
        runtime_module,
        "_available_system_ram_bytes",
        lambda: 10 * GIB,
    )

    def fake_import(
        module_name: str,
    ) -> object:
        imported_modules.append(
            module_name,
        )

        raise AssertionError(
            "Model dependencies не должны импортироваться после RAM admission reject.",
        )

    monkeypatch.setattr(
        runtime_module.importlib,
        "import_module",
        fake_import,
    )

    runtime = Qwen3VlEmbeddingRuntime(
        settings=ModelSettings(
            min_free_ram_gib=20,
        ),
        gpu_lease=gpu_lease,
    )

    with pytest.raises(
        SystemRamAdmissionError,
        match="системной RAM",
    ):
        runtime._ensure_model_sync()

    assert imported_modules == []

    assert runtime._model is None

    assert gpu_lease.acquire_calls == 1

    assert gpu_lease.release_calls == 1

    assert gpu_lease.acquired is False


def test_model_loader_rejects_unknown_system_ram(
    monkeypatch: pytest.MonkeyPatch,
    gpu_lease: FakeGpuLease,
) -> None:
    """Неизвестный RAM budget трактуется как unsafe admission."""
    imported_modules: list[str] = []

    monkeypatch.setattr(
        runtime_module,
        "_available_system_ram_bytes",
        lambda: None,
    )

    def fake_import(
        module_name: str,
    ) -> object:
        imported_modules.append(
            module_name,
        )

        raise AssertionError(
            "Torch не должен импортироваться при unknown RAM budget.",
        )

    monkeypatch.setattr(
        runtime_module.importlib,
        "import_module",
        fake_import,
    )

    runtime = Qwen3VlEmbeddingRuntime(
        settings=ModelSettings(),
        gpu_lease=gpu_lease,
    )

    with pytest.raises(
        SystemRamAdmissionError,
        match="Не удалось определить",
    ):
        runtime._ensure_model_sync()

    assert imported_modules == []

    assert runtime._model is None

    assert gpu_lease.acquire_calls == 1

    assert gpu_lease.release_calls == 1


def test_model_loader_times_out_waiting_for_vram_after_ram_admission(
    monkeypatch: pytest.MonkeyPatch,
    gpu_lease: FakeGpuLease,
) -> None:
    """После lease runtime bounded-временем ждёт VRAM."""
    monkeypatch.setattr(
        runtime_module,
        "_available_system_ram_bytes",
        lambda: 64 * GIB,
    )

    fake_cuda = SimpleNamespace(
        is_available=lambda: True,
        mem_get_info=lambda: (
            10 * GIB,
            24 * GIB,
        ),
        empty_cache=lambda: None,
        ipc_collect=lambda: None,
    )

    fake_torch = SimpleNamespace(
        cuda=fake_cuda,
    )

    original_import = runtime_module.importlib.import_module

    imported_sentence_transformers = False

    def fake_import(
        module_name: str,
    ) -> object:
        nonlocal imported_sentence_transformers

        if module_name == "torch":
            return fake_torch

        if module_name == "sentence_transformers":
            imported_sentence_transformers = True

            raise AssertionError(
                "SentenceTransformer не должен импортироваться "
                "после VRAM admission timeout.",
            )

        return original_import(
            module_name,
        )

    monkeypatch.setattr(
        runtime_module.importlib,
        "import_module",
        fake_import,
    )

    runtime = Qwen3VlEmbeddingRuntime(
        settings=ModelSettings(
            min_free_vram_gib=18,
            admission_wait_timeout_seconds=0.03,
            admission_poll_seconds=0.005,
        ),
        gpu_lease=gpu_lease,
    )

    with pytest.raises(
        GpuAdmissionError,
        match="VRAM",
    ):
        runtime._ensure_model_sync()

    assert runtime._model is None

    assert imported_sentence_transformers is False

    assert gpu_lease.acquire_calls == 1

    assert gpu_lease.release_calls == 1

    assert gpu_lease.acquired is False


def test_model_loader_uses_direct_low_cpu_gpu_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    gpu_lease: FakeGpuLease,
) -> None:
    """Большой checkpoint не должен сначала собираться в CPU RAM."""
    captured: dict[
        str,
        object,
    ] = {}

    model_dtype = object()

    monkeypatch.setattr(
        runtime_module,
        "_available_system_ram_bytes",
        lambda: 64 * GIB,
    )

    fake_cuda = SimpleNamespace(
        is_available=lambda: True,
        mem_get_info=lambda: (
            24 * GIB,
            24 * GIB,
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
        SentenceTransformer=(fake_sentence_transformer),
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
        gpu_lease=gpu_lease,
    )

    model = runtime._ensure_model_sync()

    assert captured["model_name"] == "Qwen/Qwen3-VL-Embedding-8B"

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

    assert gpu_lease.acquired is True

    runtime._release_sync(
        torch_module=fake_torch,
    )

    assert gpu_lease.acquired is False

    assert gpu_lease.release_calls == 1


def test_memory_error_during_model_load_releases_state(
    monkeypatch: pytest.MonkeyPatch,
    gpu_lease: FakeGpuLease,
) -> None:
    """MemoryError не оставляет checkpoint или GPU lease."""
    monkeypatch.setattr(
        runtime_module,
        "_available_system_ram_bytes",
        lambda: 64 * GIB,
    )

    fake_cuda = SimpleNamespace(
        is_available=lambda: True,
        mem_get_info=lambda: (
            24 * GIB,
            24 * GIB,
        ),
        empty_cache=lambda: None,
        ipc_collect=lambda: None,
    )

    fake_torch = SimpleNamespace(
        cuda=fake_cuda,
        bfloat16=object(),
    )

    def failing_model(
        model_name: str,
        **kwargs: object,
    ) -> object:
        assert model_name

        assert kwargs

        raise MemoryError(
            "simulated checkpoint RAM exhaustion",
        )

    fake_sentence_transformers = SimpleNamespace(
        SentenceTransformer=failing_model,
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
        gpu_lease=gpu_lease,
    )

    releases: list[object | None] = []

    original_release_sync = runtime._release_sync

    def recording_release_sync(
        *,
        torch_module: object | None = None,
    ) -> None:
        releases.append(
            torch_module,
        )

        original_release_sync(
            torch_module=torch_module,
        )

    monkeypatch.setattr(
        runtime,
        "_release_sync",
        recording_release_sync,
    )

    with pytest.raises(
        SystemRamAdmissionError,
        match="закончилась",
    ):
        runtime._ensure_model_sync()

    assert releases == [
        fake_torch,
    ]

    assert runtime._model is None

    assert gpu_lease.acquired is False

    assert gpu_lease.release_calls == 1


def test_cuda_oom_during_inference_releases_model(
    monkeypatch: pytest.MonkeyPatch,
    gpu_lease: FakeGpuLease,
) -> None:
    """CUDA OOM освобождает checkpoint перед retry."""
    runtime = Qwen3VlEmbeddingRuntime(
        settings=ModelSettings(),
        gpu_lease=gpu_lease,
    )

    class FakeModel:
        """Model, падающая CUDA OOM на encode."""

        def encode(
            self,
            prepared: object,
            **kwargs: object,
        ) -> object:
            assert prepared

            assert kwargs

            raise RuntimeError(
                "CUDA out of memory",
            )

    runtime._model = FakeModel()

    releases: list[bool] = []

    def fake_release_sync(
        *,
        torch_module: object | None = None,
    ) -> None:
        assert torch_module is None

        runtime._model = None

        releases.append(
            True,
        )

    monkeypatch.setattr(
        runtime,
        "_release_sync",
        fake_release_sync,
    )

    with pytest.raises(
        GpuAdmissionError,
        match="CUDA OOM",
    ):
        runtime._embed_sync(
            (
                RuntimeEmbeddingInput(
                    text="test",
                    image_bytes=None,
                    instruction=None,
                ),
            )
        )

    assert releases == [
        True,
    ]

    assert runtime._model is None


@pytest.mark.asyncio
async def test_failed_embedding_does_not_leak_semaphore(
    monkeypatch: pytest.MonkeyPatch,
    gpu_lease: FakeGpuLease,
) -> None:
    """Ошибка одного request не блокирует следующий embedding."""
    runtime = Qwen3VlEmbeddingRuntime(
        settings=ModelSettings(
            max_concurrency=1,
        ),
        gpu_lease=gpu_lease,
    )

    attempts = 0

    def fake_embed_sync(
        inputs: tuple[
            RuntimeEmbeddingInput,
            ...,
        ],
    ) -> list[list[float]]:
        nonlocal attempts

        assert inputs

        attempts += 1

        if attempts == 1:
            raise GpuAdmissionError(
                "temporary admission error",
            )

        return [[0.0] * 4096]

    monkeypatch.setattr(
        runtime,
        "_embed_sync",
        fake_embed_sync,
    )

    item = RuntimeEmbeddingInput(
        text="test",
        image_bytes=None,
        instruction=None,
    )

    with pytest.raises(
        GpuAdmissionError,
    ):
        await runtime.embed((item,))

    result = await asyncio.wait_for(
        runtime.embed((item,)),
        timeout=1,
    )

    runtime._cancel_idle_release_watchdog()

    assert attempts == 2

    assert len(result[0]) == 4096


@pytest.mark.asyncio
async def test_idle_watchdog_releases_loaded_model(
    monkeypatch: pytest.MonkeyPatch,
    gpu_lease: FakeGpuLease,
) -> None:
    """Fallback автоматически освобождает idle checkpoint."""
    runtime = Qwen3VlEmbeddingRuntime(
        settings=ModelSettings(
            idle_release_seconds=0.01,
        ),
        gpu_lease=gpu_lease,
    )

    runtime._model = object()

    runtime._last_activity_at = monotonic()

    released: list[bool] = []

    def fake_release_sync(
        *,
        torch_module: object | None = None,
    ) -> None:
        assert torch_module is None

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
