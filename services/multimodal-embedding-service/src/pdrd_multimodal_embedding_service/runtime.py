# services/multimodal-embedding-service/src/pdrd_multimodal_embedding_service/runtime.py

"""Lazy GPU runtime Qwen3-VL-Embedding-8B."""

import asyncio
import gc
import importlib
from contextlib import suppress
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from pdrd_multimodal_embedding_service.settings import (
    ModelSettings,
)


class MultimodalRuntimeError(
    RuntimeError,
):
    """Базовая ошибка GPU runtime."""


class GpuAdmissionError(
    MultimodalRuntimeError,
):
    """GPU сейчас не имеет безопасного объёма свободной VRAM."""


class MultimodalModelExecutionError(
    MultimodalRuntimeError,
):
    """Ошибка фактического model inference."""


@dataclass(frozen=True, slots=True)
class RuntimeEmbeddingInput:
    """Validated runtime input."""

    text: str | None

    image_bytes: bytes | None

    instruction: str | None


@dataclass(frozen=True, slots=True)
class RuntimeStatus:
    """Состояние CUDA и lazy-loaded модели."""

    model_loaded: bool

    cuda_available: bool

    free_vram_bytes: int | None

    total_vram_bytes: int | None


class Qwen3VlEmbeddingRuntime:
    """Управляет одним lazy-loaded GPU checkpoint."""

    def __init__(
        self,
        *,
        settings: ModelSettings,
    ) -> None:
        """Сохраняет bounded runtime settings."""
        self._settings = settings

        self._model: Any | None = None

        self._semaphore = asyncio.Semaphore(
            settings.max_concurrency,
        )

    async def embed(
        self,
        inputs: tuple[
            RuntimeEmbeddingInput,
            ...,
        ],
    ) -> list[list[float]]:
        """Последовательно строит embeddings bounded batch."""
        if not inputs:
            return []

        if (
            len(
                inputs,
            )
            > self._settings.max_batch_size
        ):
            raise MultimodalRuntimeError(
                "Размер embedding batch превышает настроенный safety limit.",
            )

        async with self._semaphore:
            return await asyncio.to_thread(
                self._embed_sync,
                inputs,
            )

    async def release(
        self,
    ) -> None:
        """Явно выгружает checkpoint и освобождает CUDA cache."""
        async with self._semaphore:
            await asyncio.to_thread(
                self._release_sync,
            )

    async def status(
        self,
    ) -> RuntimeStatus:
        """Возвращает CUDA status без загрузки checkpoint."""
        return await asyncio.to_thread(
            self._status_sync,
        )

    def _embed_sync(
        self,
        inputs: tuple[
            RuntimeEmbeddingInput,
            ...,
        ],
    ) -> list[list[float]]:
        """Выполняет blocking SentenceTransformer inference."""
        model = self._ensure_model_sync()

        prepared: list[object] = []

        opened_images: list[Any] = []

        try:
            for item in inputs:
                payload: dict[
                    str,
                    object,
                ] = {}

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

                prepared.append(payload)

            instruction = (
                inputs[0].instruction
                if len(
                    inputs,
                )
                == 1
                else None
            )

            encode_kwargs: dict[
                str,
                object,
            ] = {
                "batch_size": 1,
                "convert_to_numpy": True,
                "normalize_embeddings": True,
                "show_progress_bar": False,
                "truncate_dim": self._settings.output_dimension,
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
                        "Embedding dimension не соответствует настроенному размеру.",
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

                raise GpuAdmissionError(
                    "CUDA OOM при multimodal embedding. "
                    "Checkpoint выгружен для безопасного retry.",
                ) from error

            raise MultimodalModelExecutionError(
                f"Ошибка Qwen3-VL-Embedding inference: {type(error).__name__}: {error}",
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

    def _ensure_model_sync(
        self,
    ) -> Any:
        """Lazy-load checkpoint только после VRAM admission."""
        if self._model is not None:
            return self._model

        torch = importlib.import_module(
            "torch",
        )

        if not torch.cuda.is_available():
            raise GpuAdmissionError(
                "CUDA недоступна внутри multimodal embedding service.",
            )

        free_vram, _ = torch.cuda.mem_get_info()

        if (
            int(
                free_vram,
            )
            < self._settings.min_free_vram_bytes
        ):
            raise GpuAdmissionError(
                "Недостаточно свободной VRAM для безопасной "
                "загрузки Qwen3-VL-Embedding-8B: "
                f"free={int(free_vram)} bytes, "
                f"required={self._settings.min_free_vram_bytes} bytes.",
            )

        sentence_transformers = importlib.import_module(
            "sentence_transformers",
        )

        model_class = sentence_transformers.SentenceTransformer

        torch_dtype = getattr(
            torch,
            self._settings.dtype,
        )

        try:
            model = model_class(
                self._settings.name,
                device="cuda",
                model_kwargs={
                    "torch_dtype": torch_dtype,
                },
            )

            model.max_seq_length = self._settings.max_input_tokens

        except RuntimeError as error:
            self._release_sync()

            lowered = str(
                error,
            ).lower()

            if "out of memory" in lowered or (
                "cuda" in lowered and "memory" in lowered
            ):
                raise GpuAdmissionError(
                    "CUDA OOM при загрузке Qwen3-VL-Embedding-8B.",
                ) from error

            raise MultimodalModelExecutionError(
                "Не удалось загрузить "
                "Qwen3-VL-Embedding-8B: "
                f"{type(error).__name__}: {error}",
            ) from error

        self._model = model

        return model

    def _release_sync(
        self,
    ) -> None:
        """Удаляет model reference и очищает CUDA allocator."""
        self._model = None

        gc.collect()

        try:
            torch = importlib.import_module(
                "torch",
            )

        except ModuleNotFoundError:
            return

        if not torch.cuda.is_available():
            return

        torch.cuda.empty_cache()

        with suppress(
            RuntimeError,
        ):
            torch.cuda.ipc_collect()

    def _status_sync(
        self,
    ) -> RuntimeStatus:
        """Читает CUDA memory status без checkpoint load."""
        try:
            torch = importlib.import_module(
                "torch",
            )

        except ModuleNotFoundError:
            return RuntimeStatus(
                model_loaded=self._model is not None,
                cuda_available=False,
                free_vram_bytes=None,
                total_vram_bytes=None,
            )

        if not torch.cuda.is_available():
            return RuntimeStatus(
                model_loaded=self._model is not None,
                cuda_available=False,
                free_vram_bytes=None,
                total_vram_bytes=None,
            )

        free_vram, total_vram = torch.cuda.mem_get_info()

        return RuntimeStatus(
            model_loaded=self._model is not None,
            cuda_available=True,
            free_vram_bytes=int(
                free_vram,
            ),
            total_vram_bytes=int(
                total_vram,
            ),
        )
