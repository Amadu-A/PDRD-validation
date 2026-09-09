# services/multimodal-embedding-service/src/pdrd_multimodal_embedding_service/runtime.py

"""Lazy GPU runtime unified Qwen3-VL-Embedding checkpoint."""

import asyncio
import gc
import importlib
import logging
import os
import time
from contextlib import suppress
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from time import monotonic
from typing import Any

from pdrd_multimodal_embedding_service.gpu_lease import (
    CrossProcessFileGpuLease,
    GpuLeaseTimeoutError,
)
from pdrd_multimodal_embedding_service.settings import (
    ModelSettings,
)

LOGGER = logging.getLogger(
    __name__,
)

_CGROUP_UNLIMITED_THRESHOLD = 1 << 60


class MultimodalRuntimeError(RuntimeError):
    """Базовая ошибка GPU runtime."""


class SystemRamAdmissionError(MultimodalRuntimeError):
    """Системной RAM недостаточно для безопасного checkpoint."""


class GpuAdmissionError(MultimodalRuntimeError):
    """GPU не может безопасно принять checkpoint."""


class MultimodalModelExecutionError(MultimodalRuntimeError):
    """Ошибка model inference."""


@dataclass(frozen=True, slots=True)
class RuntimeEmbeddingInput:
    """Validated runtime input."""

    text: str | None

    image_bytes: bytes | None

    instruction: str | None


@dataclass(frozen=True, slots=True)
class RuntimeStatus:
    """Состояние CUDA и lazy-loaded model."""

    model_loaded: bool

    cuda_available: bool

    free_vram_bytes: int | None

    total_vram_bytes: int | None


def _read_integer_file(
    path: Path,
) -> int | None:
    """Best-effort читает integer из sysfs/cgroup."""
    try:
        value = path.read_text(
            encoding="utf-8",
        ).strip()

    except OSError:
        return None

    if not value or value == "max":
        return None

    try:
        parsed = int(
            value,
        )

    except ValueError:
        return None

    if parsed < 0:
        return None

    return parsed


def _linux_mem_available_bytes() -> int | None:
    """Возвращает Linux MemAvailable."""
    path = Path(
        "/proc/meminfo",
    )

    try:
        content = path.read_text(
            encoding="utf-8",
        )

    except OSError:
        return None

    for line in content.splitlines():
        if not line.startswith(
            "MemAvailable:",
        ):
            continue

        parts = line.split()

        if (
            len(
                parts,
            )
            < 2
        ):
            return None

        try:
            available_kib = int(
                parts[1],
            )

        except ValueError:
            return None

        return available_kib * 1024

    return None


def _sysconf_available_ram_bytes() -> int | None:
    """Fallback свободной RAM через POSIX sysconf."""
    sysconf = getattr(
        os,
        "sysconf",
        None,
    )

    if not callable(
        sysconf,
    ):
        return None

    try:
        pages = int(
            sysconf(
                "SC_AVPHYS_PAGES",
            )
        )

        page_size = int(
            sysconf(
                "SC_PAGE_SIZE",
            )
        )

    except (
        OSError,
        TypeError,
        ValueError,
    ):
        return None

    if pages <= 0 or page_size <= 0:
        return None

    return pages * page_size


def _cgroup_v2_available_ram_bytes() -> int | None:
    """Возвращает свободную RAM cgroup v2."""
    root = Path(
        "/sys/fs/cgroup",
    )

    limit = _read_integer_file(
        root / "memory.max",
    )

    current = _read_integer_file(
        root / "memory.current",
    )

    if limit is None or current is None:
        return None

    if limit >= _CGROUP_UNLIMITED_THRESHOLD:
        return None

    return max(
        limit - current,
        0,
    )


def _cgroup_v1_available_ram_bytes() -> int | None:
    """Возвращает свободную RAM legacy cgroup."""
    root = Path(
        "/sys/fs/cgroup/memory",
    )

    limit = _read_integer_file(
        root / "memory.limit_in_bytes",
    )

    current = _read_integer_file(
        root / "memory.usage_in_bytes",
    )

    if limit is None or current is None:
        return None

    if limit >= _CGROUP_UNLIMITED_THRESHOLD:
        return None

    return max(
        limit - current,
        0,
    )


def _available_system_ram_bytes() -> int | None:
    """Возвращает minimum host/cgroup available RAM."""
    host_available = _linux_mem_available_bytes() or _sysconf_available_ram_bytes()

    cgroup_available = (
        _cgroup_v2_available_ram_bytes() or _cgroup_v1_available_ram_bytes()
    )

    candidates = [
        value
        for value in (
            host_available,
            cgroup_available,
        )
        if value is not None
    ]

    if not candidates:
        return None

    return min(
        candidates,
    )


class Qwen3VlEmbeddingRuntime:
    """Управляет одним lazy-loaded unified checkpoint."""

    def __init__(
        self,
        *,
        settings: ModelSettings,
        gpu_lease: CrossProcessFileGpuLease | None = None,
    ) -> None:
        """Сохраняет bounded runtime settings."""
        self._settings = settings

        self._model: Any | None = None

        self._semaphore = asyncio.Semaphore(
            settings.max_concurrency,
        )

        self._last_activity_at: float | None = None

        self._idle_release_task: asyncio.Task[None] | None = None

        self._gpu_lease = (
            gpu_lease
            if gpu_lease is not None
            else CrossProcessFileGpuLease(
                path=settings.gpu_lease_path,
                poll_seconds=(settings.gpu_lease_poll_seconds),
            )
        )

    async def embed(
        self,
        inputs: tuple[
            RuntimeEmbeddingInput,
            ...,
        ],
    ) -> list[list[float]]:
        """Строит bounded embeddings."""
        if not inputs:
            return []

        if (
            len(
                inputs,
            )
            > self._settings.max_batch_size
        ):
            raise MultimodalRuntimeError(
                "Размер embedding batch превышает safety limit.",
            )

        async with self._semaphore:
            result = await asyncio.to_thread(
                self._embed_sync,
                inputs,
            )

            self._last_activity_at = monotonic()

        self._ensure_idle_release_watchdog()

        return result

    async def release(
        self,
    ) -> None:
        """Явно выгружает checkpoint и освобождает global lease."""
        async with self._semaphore:
            await asyncio.to_thread(
                self._release_sync,
            )

            self._last_activity_at = None

        self._cancel_idle_release_watchdog()

    async def status(
        self,
    ) -> RuntimeStatus:
        """Читает CUDA status без model load."""
        return await asyncio.to_thread(
            self._status_sync,
        )

    def _ensure_idle_release_watchdog(
        self,
    ) -> None:
        """Запускает один idle watchdog."""
        task = self._idle_release_task

        if task is not None and not task.done():
            return

        self._idle_release_task = asyncio.create_task(
            self._release_after_idle(),
        )

    def _cancel_idle_release_watchdog(
        self,
    ) -> None:
        """Останавливает watchdog."""
        task = self._idle_release_task

        if task is None:
            return

        if not task.done():
            task.cancel()

        self._idle_release_task = None

    async def _release_after_idle(
        self,
    ) -> None:
        """Освобождает checkpoint после idle timeout."""
        try:
            while True:
                last_activity = self._last_activity_at

                if last_activity is None:
                    return

                remaining = self._settings.idle_release_seconds - (
                    monotonic() - last_activity
                )

                if remaining > 0:
                    await asyncio.sleep(
                        remaining,
                    )

                async with self._semaphore:
                    if self._model is None:
                        self._last_activity_at = None

                        return

                    last_activity = self._last_activity_at

                    if last_activity is None:
                        return

                    if (
                        monotonic() - last_activity
                        < self._settings.idle_release_seconds
                    ):
                        continue

                    await asyncio.to_thread(
                        self._release_sync,
                    )

                    self._last_activity_at = None

                    return

        except asyncio.CancelledError:
            return

        finally:
            if self._idle_release_task is asyncio.current_task():
                self._idle_release_task = None

    def _embed_sync(
        self,
        inputs: tuple[
            RuntimeEmbeddingInput,
            ...,
        ],
    ) -> list[list[float]]:
        """Выполняет SentenceTransformer inference."""
        model = self._ensure_model_sync()

        prepared: list[object] = []

        opened_images: list[Any] = []

        try:
            for item in inputs:
                payload: dict[str, object] = {}

                if item.text is not None and item.text.strip():
                    payload["text"] = item.text.strip()

                if item.image_bytes is not None:
                    image_module = importlib.import_module(
                        "PIL.Image",
                    )

                    image = image_module.open(
                        BytesIO(
                            item.image_bytes,
                        )
                    )

                    image.load()

                    image = image.convert(
                        "RGB",
                    )

                    opened_images.append(
                        image,
                    )

                    payload["image"] = image

                prepared.append(
                    payload,
                )

            instruction = (
                inputs[0].instruction
                if len(
                    inputs,
                )
                == 1
                else None
            )

            encode_kwargs: dict[str, object] = {
                "batch_size": 1,
                "convert_to_numpy": True,
                "normalize_embeddings": True,
                "show_progress_bar": False,
                "truncate_dim": (self._settings.output_dimension),
            }

            if instruction is not None and instruction.strip():
                encode_kwargs["prompt"] = instruction.strip()

            embeddings = model.encode(
                prepared,
                **encode_kwargs,
            )

            result: list[list[float]] = []

            for vector in embeddings:
                converted = [
                    float(
                        value,
                    )
                    for value in vector.tolist()
                ]

                if (
                    len(
                        converted,
                    )
                    != self._settings.output_dimension
                ):
                    raise MultimodalModelExecutionError(
                        "Embedding dimension не соответствует configured dimension.",
                    )

                result.append(
                    converted,
                )

            return result

        except RuntimeError as error:
            lowered = str(
                error,
            ).lower()

            if "out of memory" in lowered or (
                "cuda" in lowered and "memory" in lowered
            ):
                self._release_sync()

                LOGGER.exception(
                    "qwen3_vl_embedding_cuda_oom",
                )

                raise GpuAdmissionError(
                    "CUDA OOM при embedding. Checkpoint и GPU lease освобождены.",
                ) from error

            LOGGER.exception(
                "qwen3_vl_embedding_failed",
            )

            raise MultimodalModelExecutionError(
                f"Ошибка embedding inference: {type(error).__name__}: {error}",
            ) from error

        finally:
            for image in opened_images:
                close = getattr(
                    image,
                    "close",
                    None,
                )

                if callable(
                    close,
                ):
                    close()

    def _require_system_ram_admission(
        self,
    ) -> int:
        """Проверяет RAM до импорта CUDA/model dependencies."""
        available_ram_bytes = _available_system_ram_bytes()

        if available_ram_bytes is None:
            LOGGER.error(
                "qwen3_vl_ram_admission_unknown model=%s",
                self._settings.name,
            )

            raise SystemRamAdmissionError(
                "Не удалось определить объём доступной "
                "системной RAM для безопасной загрузки "
                f"{self._settings.name}.",
            )

        if available_ram_bytes < self._settings.min_free_ram_bytes:
            LOGGER.warning(
                (
                    "qwen3_vl_ram_admission_rejected "
                    "model=%s "
                    "available_ram_bytes=%s "
                    "required_ram_bytes=%s"
                ),
                self._settings.name,
                available_ram_bytes,
                self._settings.min_free_ram_bytes,
            )

            raise SystemRamAdmissionError(
                "Недостаточно свободной системной RAM "
                "для безопасной загрузки "
                f"{self._settings.name}: "
                f"available={available_ram_bytes} bytes, "
                f"required="
                f"{self._settings.min_free_ram_bytes} bytes.",
            )

        return available_ram_bytes

    def _wait_for_vram_admission(
        self,
        *,
        torch: Any,
    ) -> tuple[
        int,
        int,
    ]:
        """Bounded-временем ждёт VRAM после global GPU lease."""
        if not torch.cuda.is_available():
            raise GpuAdmissionError(
                "CUDA недоступна.",
            )

        deadline = time.monotonic() + self._settings.admission_wait_timeout_seconds

        last_free_vram: int | None = None

        total_vram_bytes = 0

        while True:
            free_vram, total_vram = torch.cuda.mem_get_info()

            last_free_vram = int(
                free_vram,
            )

            total_vram_bytes = int(
                total_vram,
            )

            if last_free_vram >= self._settings.min_free_vram_bytes:
                return (
                    last_free_vram,
                    total_vram_bytes,
                )

            if time.monotonic() >= deadline:
                raise GpuAdmissionError(
                    "Истёк timeout ожидания VRAM: "
                    f"free={last_free_vram}, "
                    f"required="
                    f"{self._settings.min_free_vram_bytes}.",
                )

            LOGGER.info(
                ("embedding_vram_wait free_vram_bytes=%s required_vram_bytes=%s"),
                last_free_vram,
                self._settings.min_free_vram_bytes,
            )

            time.sleep(
                self._settings.admission_poll_seconds,
            )

    def _ensure_model_sync(
        self,
    ) -> Any:
        """Получает global lease, проверяет RAM/VRAM и загружает model."""
        if self._model is not None:
            return self._model

        try:
            self._gpu_lease.acquire(
                timeout_seconds=(self._settings.gpu_lease_timeout_seconds),
            )

        except GpuLeaseTimeoutError as error:
            raise GpuAdmissionError(
                "Не удалось получить global GPU lease.",
            ) from error

        torch: Any | None = None

        try:
            # Этот guard принципиально расположен
            # ДО import torch/sentence_transformers.
            available_ram_bytes = self._require_system_ram_admission()

            torch = importlib.import_module(
                "torch",
            )

            (
                free_vram_bytes,
                total_vram_bytes,
            ) = self._wait_for_vram_admission(
                torch=torch,
            )

            sentence_transformers = importlib.import_module(
                "sentence_transformers",
            )

            model_class = sentence_transformers.SentenceTransformer

            model_dtype = getattr(
                torch,
                self._settings.dtype,
            )

            LOGGER.info(
                (
                    "qwen3_vl_model_load_started "
                    "model=%s dtype=%s "
                    "available_ram_bytes=%s "
                    "free_vram_bytes=%s "
                    "total_vram_bytes=%s "
                    "device_map=cuda:0"
                ),
                self._settings.name,
                self._settings.dtype,
                available_ram_bytes,
                free_vram_bytes,
                total_vram_bytes,
            )

            model = model_class(
                self._settings.name,
                model_kwargs={
                    "dtype": model_dtype,
                    "device_map": "cuda:0",
                    "low_cpu_mem_usage": True,
                },
            )

            model.max_seq_length = self._settings.max_input_tokens

            self._model = model

            LOGGER.info(
                "qwen3_vl_model_load_completed model=%s",
                self._settings.name,
            )

            return model

        except Exception as error:
            self._release_sync(
                torch_module=torch,
            )

            if isinstance(
                error,
                (
                    GpuAdmissionError,
                    SystemRamAdmissionError,
                    MultimodalModelExecutionError,
                ),
            ):
                raise

            if isinstance(
                error,
                MemoryError,
            ):
                raise SystemRamAdmissionError(
                    "Системная RAM закончилась при model load.",
                ) from error

            lowered = str(
                error,
            ).lower()

            if (
                "cannot allocate memory" in lowered
                or "bad_alloc" in lowered
                or "bad alloc" in lowered
            ):
                raise SystemRamAdmissionError(
                    "Не удалось выделить RAM при model load.",
                ) from error

            if "out of memory" in lowered or (
                "cuda" in lowered and "memory" in lowered
            ):
                raise GpuAdmissionError(
                    "CUDA OOM при model load.",
                ) from error

            raise MultimodalModelExecutionError(
                "Не удалось загрузить "
                f"{self._settings.name}: "
                f"{type(error).__name__}: {error}",
            ) from error

    def _release_sync(
        self,
        *,
        torch_module: Any | None = None,
    ) -> None:
        """Удаляет model, CUDA cache и global GPU lease."""
        model_was_loaded = self._model is not None

        self._model = None

        try:
            gc.collect()

            torch = torch_module

            # Важный случай:
            # admission мог упасть ещё до import torch.
            # Тогда импортировать torch только ради cleanup нельзя.
            if torch is None and not model_was_loaded:
                return

            if torch is None:
                try:
                    torch = importlib.import_module(
                        "torch",
                    )

                except ModuleNotFoundError:
                    return

            cuda = getattr(
                torch,
                "cuda",
                None,
            )

            if cuda is None:
                return

            is_available = getattr(
                cuda,
                "is_available",
                None,
            )

            if (
                not callable(
                    is_available,
                )
                or not is_available()
            ):
                return

            empty_cache = getattr(
                cuda,
                "empty_cache",
                None,
            )

            if callable(
                empty_cache,
            ):
                empty_cache()

            ipc_collect = getattr(
                cuda,
                "ipc_collect",
                None,
            )

            if callable(
                ipc_collect,
            ):
                with suppress(
                    RuntimeError,
                ):
                    ipc_collect()

            if model_was_loaded:
                LOGGER.info(
                    "qwen3_vl_model_released",
                )

        finally:
            self._gpu_lease.release()

    def _status_sync(
        self,
    ) -> RuntimeStatus:
        """Читает CUDA memory без checkpoint load."""
        try:
            torch = importlib.import_module(
                "torch",
            )

        except ModuleNotFoundError:
            return RuntimeStatus(
                model_loaded=(self._model is not None),
                cuda_available=False,
                free_vram_bytes=None,
                total_vram_bytes=None,
            )

        if not torch.cuda.is_available():
            return RuntimeStatus(
                model_loaded=(self._model is not None),
                cuda_available=False,
                free_vram_bytes=None,
                total_vram_bytes=None,
            )

        free_vram, total_vram = torch.cuda.mem_get_info()

        return RuntimeStatus(
            model_loaded=(self._model is not None),
            cuda_available=True,
            free_vram_bytes=int(
                free_vram,
            ),
            total_vram_bytes=int(
                total_vram,
            ),
        )
