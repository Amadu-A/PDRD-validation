# services/analysis-service/tests/unit/test_ollama_residency.py

"""Unit tests bounded Ollama VLM residency."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from pdrd_analysis_service.application.ports.gpu import (
    GpuMemorySnapshot,
)
from pdrd_analysis_service.domain.analysis import (
    GenerationMetrics,
    GenerationResult,
)
from pdrd_analysis_service.infrastructure.ollama import (
    OllamaStructuredVisionModel,
)


class RecordingGpuCoordinator:
    """Fake GPU coordinator с подсчётом lease lifecycle."""

    def __init__(
        self,
    ) -> None:
        """Инициализирует counters."""
        self.enters = 0
        self.exits = 0

    @asynccontextmanager
    async def reserve(
        self,
        *,
        required_free_vram_bytes: int,
    ) -> AsyncIterator[GpuMemorySnapshot]:
        """Выдаёт fake GPU lease."""
        assert required_free_vram_bytes > 0

        self.enters += 1

        try:
            yield GpuMemorySnapshot(
                cuda_available=True,
                free_vram_bytes=24 * 1024**3,
                total_vram_bytes=24 * 1024**3,
            )

        finally:
            self.exits += 1


def _generation_result() -> GenerationResult:
    """Возвращает deterministic fake generation."""
    return GenerationResult(
        payload={
            "ok": True,
        },
        metrics=GenerationMetrics(
            attempt=1,
            done_reason="stop",
            requested_num_predict=100,
            total_duration_ms=10.0,
            load_duration_ms=2.0,
            prompt_eval_count=10,
            eval_count=5,
            content_length=20,
            thinking_length=0,
        ),
    )


def _model(
    coordinator: RecordingGpuCoordinator,
) -> OllamaStructuredVisionModel:
    """Создаёт adapter для lifecycle tests."""
    return OllamaStructuredVisionModel(
        base_url="http://ollama:11434",
        model="qwen3-vl:8b-instruct",
        request_timeout_seconds=30,
        connect_timeout_seconds=5,
        health_timeout_seconds=5,
        num_ctx=32768,
        max_retries=2,
        keep_alive="60s",
        max_retry_num_predict=14000,
        gpu_coordinator=coordinator,
        min_free_vram_bytes=1,
        unload_timeout_seconds=5,
        unload_poll_seconds=0.1,
    )


async def _call_model(
    model: OllamaStructuredVisionModel,
    *,
    stage: str,
) -> GenerationResult:
    """Выполняет один generic fake generation call."""
    return await model.generate_json(
        prompt="Prompt",
        schema={
            "type": "object",
        },
        num_predict=100,
        seed=100,
        stage=stage,
        image_bytes=None,
    )


async def test_two_calls_inside_residency_use_one_gpu_lease_and_one_unload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Два VLM calls одного stage не перезагружают model между собой."""
    coordinator = RecordingGpuCoordinator()

    model = _model(
        coordinator,
    )

    locked_calls: list[str] = []

    unload_calls = 0

    async def fake_generate_json_locked(
        *,
        prompt: str,
        schema: dict[str, Any],
        num_predict: int,
        seed: int,
        stage: str,
        image_bytes: bytes | None,
    ) -> GenerationResult:
        del prompt
        del schema
        del num_predict
        del seed
        del image_bytes

        locked_calls.append(
            stage,
        )

        return _generation_result()

    async def fake_unload_model() -> None:
        nonlocal unload_calls

        unload_calls += 1

    monkeypatch.setattr(
        model,
        "_generate_json_locked",
        fake_generate_json_locked,
    )

    monkeypatch.setattr(
        model,
        "_unload_model",
        fake_unload_model,
    )

    async with model.residency_scope():
        await _call_model(
            model,
            stage="batch:1",
        )

        await _call_model(
            model,
            stage="batch:2",
        )

    assert locked_calls == [
        "batch:1",
        "batch:2",
    ]

    assert coordinator.enters == 1
    assert coordinator.exits == 1

    assert unload_calls == 1


async def test_call_without_residency_keeps_legacy_safe_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Одиночный call по-прежнему unloads model перед release lease."""
    coordinator = RecordingGpuCoordinator()

    model = _model(
        coordinator,
    )

    unload_calls = 0

    async def fake_generate_json_locked(
        **kwargs: Any,
    ) -> GenerationResult:
        assert kwargs["stage"] == "single"

        return _generation_result()

    async def fake_unload_model() -> None:
        nonlocal unload_calls

        unload_calls += 1

    monkeypatch.setattr(
        model,
        "_generate_json_locked",
        fake_generate_json_locked,
    )

    monkeypatch.setattr(
        model,
        "_unload_model",
        fake_unload_model,
    )

    await _call_model(
        model,
        stage="single",
    )

    assert coordinator.enters == 1
    assert coordinator.exits == 1

    assert unload_calls == 1


async def test_nested_residency_does_not_reacquire_gpu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nested bounded scopes используют один внешний GPU lease."""
    coordinator = RecordingGpuCoordinator()

    model = _model(
        coordinator,
    )

    unload_calls = 0

    async def fake_generate_json_locked(
        **kwargs: Any,
    ) -> GenerationResult:
        assert kwargs["stage"] == "nested"

        return _generation_result()

    async def fake_unload_model() -> None:
        nonlocal unload_calls

        unload_calls += 1

    monkeypatch.setattr(
        model,
        "_generate_json_locked",
        fake_generate_json_locked,
    )

    monkeypatch.setattr(
        model,
        "_unload_model",
        fake_unload_model,
    )

    async with model.residency_scope(), model.residency_scope():
        await _call_model(
            model,
            stage="nested",
        )

    assert coordinator.enters == 1
    assert coordinator.exits == 1

    assert unload_calls == 1


async def test_residency_releases_gpu_after_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Исключение внутри stage не оставляет GPU lease/model resident."""
    coordinator = RecordingGpuCoordinator()

    model = _model(
        coordinator,
    )

    unload_calls = 0

    async def fake_unload_model() -> None:
        nonlocal unload_calls

        unload_calls += 1

    monkeypatch.setattr(
        model,
        "_unload_model",
        fake_unload_model,
    )

    with pytest.raises(
        RuntimeError,
        match="controlled failure",
    ):
        async with model.residency_scope():
            raise RuntimeError(
                "controlled failure",
            )

    assert coordinator.enters == 1
    assert coordinator.exits == 1

    assert unload_calls == 1
