# services/analysis-service/src/pdrd_analysis_service/infrastructure/ollama.py

"""Structured VLM adapter shared Ollama."""

import asyncio
import base64
import json
import logging
from typing import Any

import httpx

from pdrd_analysis_service.application.ports.gpu import (
    GpuCoordinationError,
    GpuCoordinator,
)
from pdrd_analysis_service.application.ports.vision_model import (
    VisionModelError,
)
from pdrd_analysis_service.domain.analysis import (
    GenerationMetrics,
    GenerationResult,
)

logger = logging.getLogger(
    "uvicorn.error",
)

_CONTEXT_EXHAUSTION_MARGIN_TOKENS = 64


class OllamaStructuredVisionModel:
    """Structured VLM provider с global GPU lease."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        request_timeout_seconds: float,
        connect_timeout_seconds: float,
        health_timeout_seconds: float,
        num_ctx: int,
        max_retries: int,
        keep_alive: str,
        max_retry_num_predict: int,
        gpu_coordinator: GpuCoordinator,
        min_free_vram_bytes: int,
        unload_timeout_seconds: float,
        unload_poll_seconds: float,
    ) -> None:
        """Сохраняет runtime параметры."""
        self._base_url = base_url.rstrip(
            "/",
        )

        self._model = model

        self._request_timeout_seconds = request_timeout_seconds

        self._connect_timeout_seconds = connect_timeout_seconds

        self._health_timeout_seconds = health_timeout_seconds

        self._num_ctx = num_ctx

        self._max_retries = max_retries

        self._keep_alive = keep_alive

        self._max_retry_num_predict = max_retry_num_predict

        self._gpu_coordinator = gpu_coordinator

        self._min_free_vram_bytes = min_free_vram_bytes

        self._unload_timeout_seconds = unload_timeout_seconds

        self._unload_poll_seconds = unload_poll_seconds

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
        """Вызывает Ollama только внутри global GPU lease."""
        try:
            async with self._gpu_coordinator.reserve(
                required_free_vram_bytes=(self._min_free_vram_bytes),
            ):
                try:
                    return await self._generate_json_locked(
                        prompt=prompt,
                        schema=schema,
                        num_predict=num_predict,
                        seed=seed,
                        stage=stage,
                        image_bytes=image_bytes,
                    )

                finally:
                    await self._unload_model()

        except GpuCoordinationError as error:
            raise VisionModelError(
                f"GPU недоступен для VLM stage {stage}: {error}",
            ) from error

    async def _generate_json_locked(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        num_predict: int,
        seed: int,
        stage: str,
        image_bytes: bytes | None,
    ) -> GenerationResult:
        """Выполняет structured generation при уже захваченном GPU."""
        encoded_image: str | None = None

        if image_bytes is not None:
            encoded_image = base64.b64encode(
                image_bytes,
            ).decode(
                "ascii",
            )

        last_content = ""

        last_metrics: GenerationMetrics | None = None

        for attempt in range(
            1,
            self._max_retries + 1,
        ):
            attempt_num_predict = (
                num_predict
                if attempt == 1
                else min(
                    num_predict * 2,
                    self._max_retry_num_predict,
                )
            )

            attempt_prompt = prompt

            if attempt > 1:
                attempt_prompt += (
                    "\n\nПРЕДЫДУЩИЙ ОТВЕТ БЫЛ ОБРЕЗАН "
                    "ИЛИ НЕ ЯВЛЯЛСЯ ПОЛНЫМ JSON. "
                    "Ответь существенно короче. "
                    "Не повторяй рассуждения. "
                    "Верни только полный JSON."
                )

            message: dict[str, Any] = {
                "role": "user",
                "content": attempt_prompt,
            }

            if encoded_image is not None:
                message["images"] = [
                    encoded_image,
                ]

            logger.info(
                "[VLM:%s] START attempt=%s num_predict=%s image=%s",
                stage,
                attempt,
                attempt_num_predict,
                image_bytes is not None,
            )

            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(
                        self._request_timeout_seconds,
                        connect=self._connect_timeout_seconds,
                    ),
                ) as client:
                    response = await client.post(
                        f"{self._base_url}/api/chat",
                        json={
                            "model": self._model,
                            "messages": [
                                message,
                            ],
                            "stream": False,
                            "think": False,
                            "format": schema,
                            "keep_alive": self._keep_alive,
                            "options": {
                                "temperature": 0.0,
                                "seed": seed + attempt,
                                "repeat_penalty": 1.10,
                                "num_ctx": self._num_ctx,
                                "num_predict": (attempt_num_predict),
                            },
                        },
                    )

                    response.raise_for_status()

            except httpx.HTTPStatusError as error:
                raise VisionModelError(
                    "Ollama вернул ошибку "
                    f"на этапе {stage}: "
                    f"{error.response.status_code}: "
                    f"{error.response.text[:1500]}",
                ) from error

            except httpx.HTTPError as error:
                raise VisionModelError(
                    f"Не удалось обратиться к Ollama на этапе {stage}: {error}",
                ) from error

            try:
                response_payload = response.json()

            except ValueError as error:
                raise VisionModelError(
                    f"Ollama вернул не-JSON HTTP-ответ на этапе {stage}.",
                ) from error

            message_payload = response_payload.get(
                "message",
                {},
            )

            last_content = str(
                message_payload.get(
                    "content",
                    "",
                )
            )

            thinking = str(
                message_payload.get(
                    "thinking",
                    "",
                )
            )

            last_metrics = GenerationMetrics(
                attempt=attempt,
                done_reason=response_payload.get(
                    "done_reason",
                ),
                requested_num_predict=(attempt_num_predict),
                total_duration_ms=round(
                    float(
                        response_payload.get(
                            "total_duration",
                            0,
                        )
                        or 0
                    )
                    / 1_000_000,
                    2,
                ),
                load_duration_ms=round(
                    float(
                        response_payload.get(
                            "load_duration",
                            0,
                        )
                        or 0
                    )
                    / 1_000_000,
                    2,
                ),
                prompt_eval_count=response_payload.get(
                    "prompt_eval_count",
                ),
                eval_count=response_payload.get(
                    "eval_count",
                ),
                content_length=len(
                    last_content,
                ),
                thinking_length=len(
                    thinking,
                ),
            )

            logger.info(
                "[VLM:%s] DONE attempt=%s "
                "reason=%s prompt_tokens=%s "
                "output_tokens=%s content_chars=%s "
                "thinking_chars=%s",
                stage,
                attempt,
                last_metrics.done_reason,
                last_metrics.prompt_eval_count,
                last_metrics.eval_count,
                last_metrics.content_length,
                last_metrics.thinking_length,
            )

            try:
                parsed = json.loads(
                    last_content,
                )

            except json.JSONDecodeError as error:
                if self._context_window_exhausted(
                    last_metrics,
                ):
                    raise VisionModelError(
                        "Контекст Ollama исчерпан "
                        f"на этапе {stage}: "
                        f"num_ctx={self._num_ctx}, "
                        "prompt_tokens="
                        f"{last_metrics.prompt_eval_count}, "
                        "output_tokens="
                        f"{last_metrics.eval_count}. "
                        "Повтор с увеличенным num_predict пропущен, "
                        "потому что он не помещается в configured context window.",
                    ) from error

                if attempt < self._max_retries:
                    continue

                raise VisionModelError(
                    "Модель не смогла сформировать "
                    "корректный JSON "
                    f"на этапе {stage}. "
                    f"response={last_content[:1800]}",
                ) from error

            if not isinstance(
                parsed,
                dict,
            ):
                raise VisionModelError(
                    "Structured VLM вернула JSON не объектного типа.",
                )

            return GenerationResult(
                payload=parsed,
                metrics=last_metrics,
            )

        raise VisionModelError(
            f"Не удалось получить JSON на этапе {stage}.",
        )

    def _context_window_exhausted(
        self,
        metrics: GenerationMetrics | None,
    ) -> bool:
        """Определяет, что generation упёрлась именно в context window."""
        if metrics is None or metrics.done_reason != "length":
            return False

        prompt_tokens = metrics.prompt_eval_count
        output_tokens = metrics.eval_count

        if prompt_tokens is None or output_tokens is None:
            return False

        used_tokens = prompt_tokens + output_tokens

        return used_tokens >= self._num_ctx - _CONTEXT_EXHAUSTION_MARGIN_TOKENS

    async def _unload_model(
        self,
    ) -> None:
        """Явно выгружает Ollama model до release global lease."""
        try:
            async with httpx.AsyncClient(
                timeout=self._health_timeout_seconds,
            ) as client:
                response = await client.post(
                    f"{self._base_url}/api/generate",
                    json={
                        "model": self._model,
                        "keep_alive": 0,
                    },
                )

                response.raise_for_status()

        except httpx.HTTPError as error:
            raise VisionModelError(
                "Не удалось явно выгрузить VLM из Ollama.",
            ) from error

        deadline = asyncio.get_running_loop().time() + self._unload_timeout_seconds

        while True:
            if not await self._is_model_loaded():
                return

            if asyncio.get_running_loop().time() >= deadline:
                raise VisionModelError(
                    "Ollama VLM не освободила GPU за configured unload timeout.",
                )

            await asyncio.sleep(
                self._unload_poll_seconds,
            )

    async def _is_model_loaded(
        self,
    ) -> bool:
        """Проверяет runtime residency model."""
        try:
            async with httpx.AsyncClient(
                timeout=self._health_timeout_seconds,
            ) as client:
                response = await client.get(
                    f"{self._base_url}/api/ps",
                )

                response.raise_for_status()

        except httpx.HTTPError as error:
            raise VisionModelError(
                "Не удалось проверить Ollama model residency.",
            ) from error

        models = response.json().get(
            "models",
            [],
        )

        if not isinstance(
            models,
            list,
        ):
            return False

        return any(
            isinstance(
                item,
                dict,
            )
            and str(
                item.get(
                    "name",
                    "",
                )
            )
            == self._model
            for item in models
        )

    async def is_ready(
        self,
    ) -> bool:
        """Проверяет наличие configured VLM без model load."""
        try:
            async with httpx.AsyncClient(
                timeout=self._health_timeout_seconds,
            ) as client:
                response = await client.get(
                    f"{self._base_url}/api/tags",
                )

                response.raise_for_status()

        except httpx.HTTPError:
            return False

        models = response.json().get(
            "models",
            [],
        )

        if not isinstance(
            models,
            list,
        ):
            return False

        names = {
            str(
                item.get(
                    "name",
                    "",
                )
            )
            for item in models
            if isinstance(
                item,
                dict,
            )
        }

        return self._model in names
