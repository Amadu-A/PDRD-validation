# services/analysis-service/tests/unit/test_vllm_cache.py

"""Unit tests project-local persistent VLM request cache."""

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest
from pdrd_analysis_service.core.settings import (
    Settings,
)
from pdrd_analysis_service.domain.analysis import (
    GenerationMetrics,
    GenerationResult,
)
from pdrd_analysis_service.infrastructure.vllm_cache import (
    CachedStructuredVisionModel,
)


class RecordingVisionModel:
    """Fake VLM с наблюдаемым количеством provider calls."""

    def __init__(
        self,
        *,
        payload: dict[str, Any],
        delay_seconds: float = 0.0,
    ) -> None:
        """Сохраняет canonical fake payload."""
        self.payload = payload

        self.delay_seconds = delay_seconds

        self.calls = 0

    async def generate_json(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        num_predict: int,
        seed: int,
        stage: str,
        image_bytes: bytes | None = None,
    ) -> GenerationResult:
        """Возвращает prepared payload и считает provider calls."""
        assert prompt
        assert schema
        assert num_predict > 0
        assert seed > 0
        assert stage

        del image_bytes

        self.calls += 1

        if self.delay_seconds > 0:
            await asyncio.sleep(
                self.delay_seconds,
            )

        return GenerationResult(
            payload=dict(
                self.payload,
            ),
            metrics=GenerationMetrics(
                attempt=1,
                done_reason="stop",
                requested_num_predict=(num_predict),
                total_duration_ms=10.0,
                load_duration_ms=0.0,
                prompt_eval_count=100,
                eval_count=20,
                content_length=100,
                thinking_length=0,
            ),
        )

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает readiness."""
        return True


def _cache_json_files(
    root_path: Path,
) -> list[Path]:
    """Просматривает локальный test-cache за пределами event loop."""
    return list(root_path.rglob("*.json"))


def _model(
    *,
    delegate: RecordingVisionModel,
    root_path: Path,
    namespace: str = "test-v1",
    enabled: bool = True,
    ttl_seconds: int = 3600,
) -> CachedStructuredVisionModel:
    """Строит cached VLM decorator."""
    return CachedStructuredVisionModel(
        delegate=delegate,
        provider_identity=("http://shared-vlm:8000/v1|shared-vlm"),
        root_path=root_path,
        namespace=namespace,
        ttl_seconds=ttl_seconds,
        enabled=enabled,
    )


async def _generate(
    model: CachedStructuredVisionModel,
    *,
    prompt: str = "Проверка.",
    seed: int = 100,
    image_bytes: bytes | None = b"image-a",
) -> GenerationResult:
    """Выполняет один одинаково параметризованный fake request."""
    return await model.generate_json(
        prompt=prompt,
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "value": {
                    "type": "string",
                },
            },
            "required": [
                "value",
            ],
        },
        num_predict=4000,
        seed=seed,
        stage="page_understanding:1",
        image_bytes=image_bytes,
    )


async def test_cache_persists_across_wrapper_instances(
    tmp_path: Path,
) -> None:
    """Новый decorator reuse'ит canonical response без provider call."""
    first_delegate = RecordingVisionModel(
        payload={
            "value": "canonical",
        },
    )

    first_model = _model(
        delegate=first_delegate,
        root_path=tmp_path,
    )

    first_result = await _generate(
        first_model,
    )

    assert first_result.payload == {
        "value": "canonical",
    }

    assert first_result.metrics.done_reason == "stop"

    assert first_delegate.calls == 1

    second_delegate = RecordingVisionModel(
        payload={
            "value": ("different-provider-answer"),
        },
    )

    second_model = _model(
        delegate=second_delegate,
        root_path=tmp_path,
    )

    second_result = await _generate(
        second_model,
    )

    assert second_result.payload == {
        "value": "canonical",
    }

    assert second_result.metrics.done_reason == "cache_hit"

    assert second_result.metrics.total_duration_ms == 0.0

    assert second_delegate.calls == 0


async def test_cache_key_changes_with_exact_request(
    tmp_path: Path,
) -> None:
    """Prompt, seed и image участвуют в request identity."""
    delegate = RecordingVisionModel(
        payload={
            "value": "answer",
        },
    )

    model = _model(
        delegate=delegate,
        root_path=tmp_path,
    )

    await _generate(
        model,
    )

    await _generate(
        model,
    )

    await _generate(
        model,
        prompt="Другая проверка.",
    )

    await _generate(
        model,
        seed=101,
    )

    await _generate(
        model,
        image_bytes=b"image-b",
    )

    assert delegate.calls == 4


async def test_namespace_invalidates_previous_cache(
    tmp_path: Path,
) -> None:
    """Namespace позволяет явно инвалидировать responses."""
    first_delegate = RecordingVisionModel(
        payload={
            "value": "v1",
        },
    )

    first_model = _model(
        delegate=first_delegate,
        root_path=tmp_path,
        namespace="v1",
    )

    await _generate(
        first_model,
    )

    second_delegate = RecordingVisionModel(
        payload={
            "value": "v2",
        },
    )

    second_model = _model(
        delegate=second_delegate,
        root_path=tmp_path,
        namespace="v2",
    )

    result = await _generate(
        second_model,
    )

    assert result.payload == {
        "value": "v2",
    }

    assert second_delegate.calls == 1


async def test_single_flight_coalesces_identical_requests(
    tmp_path: Path,
) -> None:
    """Concurrent identical misses выполняют один provider call."""
    delegate = RecordingVisionModel(
        payload={
            "value": "canonical",
        },
        delay_seconds=0.05,
    )

    model = _model(
        delegate=delegate,
        root_path=tmp_path,
    )

    results = await asyncio.gather(
        *(
            _generate(
                model,
            )
            for _ in range(
                8,
            )
        )
    )

    assert delegate.calls == 1

    assert {result.payload["value"] for result in results} == {
        "canonical",
    }

    assert sum((result.metrics.done_reason == "cache_hit") for result in results) == 7


async def test_expired_cache_is_refreshed(
    tmp_path: Path,
) -> None:
    """Expired record больше не считается canonical response."""
    first_delegate = RecordingVisionModel(
        payload={
            "value": "old",
        },
    )

    first_model = _model(
        delegate=first_delegate,
        root_path=tmp_path,
        ttl_seconds=60,
    )

    await _generate(
        first_model,
    )

    cache_files = await asyncio.to_thread(
        _cache_json_files,
        tmp_path,
    )

    assert (
        len(
            cache_files,
        )
        == 1
    )

    cache_path = cache_files[0]

    record = await asyncio.to_thread(
        cache_path.read_text,
        encoding="utf-8",
    )
    record = json.loads(record)

    record["created_at"] = time.time() - 120

    await asyncio.to_thread(
        cache_path.write_text,
        json.dumps(
            record,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    second_delegate = RecordingVisionModel(
        payload={
            "value": "fresh",
        },
    )

    second_model = _model(
        delegate=second_delegate,
        root_path=tmp_path,
        ttl_seconds=60,
    )

    result = await _generate(
        second_model,
    )

    assert result.payload == {
        "value": "fresh",
    }

    assert second_delegate.calls == 1


async def test_disabled_cache_calls_delegate_each_time(
    tmp_path: Path,
) -> None:
    """Disabled cache сохраняет прежний provider contract."""
    delegate = RecordingVisionModel(
        payload={
            "value": "answer",
        },
    )

    model = _model(
        delegate=delegate,
        root_path=tmp_path,
        enabled=False,
    )

    await _generate(
        model,
    )

    await _generate(
        model,
    )

    assert delegate.calls == 2

    assert (
        await asyncio.to_thread(
            _cache_json_files,
            tmp_path,
        )
        == []
    )


async def test_readiness_is_delegated_to_provider(
    tmp_path: Path,
) -> None:
    """Cache не маскирует shared-vlm readiness."""
    delegate = RecordingVisionModel(
        payload={
            "value": "answer",
        },
    )

    model = _model(
        delegate=delegate,
        root_path=tmp_path,
    )

    assert await model.is_ready() is True


def test_settings_read_nested_cache_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Nested env contract конфигурирует project cache."""
    monkeypatch.setenv(
        "ANALYSIS_SERVICE_VLM__CACHE__ENABLED",
        "true",
    )

    monkeypatch.setenv(
        ("ANALYSIS_SERVICE_VLM__CACHE__ROOT_PATH"),
        str(
            tmp_path,
        ),
    )

    monkeypatch.setenv(
        ("ANALYSIS_SERVICE_VLM__CACHE__NAMESPACE"),
        "test-v2",
    )

    monkeypatch.setenv(
        ("ANALYSIS_SERVICE_VLM__CACHE__TTL_SECONDS"),
        "7200",
    )

    settings = Settings(
        _env_file=None,
    )

    assert settings.vlm.cache.enabled is True

    assert settings.vlm.cache.root_path == tmp_path

    assert settings.vlm.cache.namespace == "test-v2"

    assert settings.vlm.cache.ttl_seconds == 7200
