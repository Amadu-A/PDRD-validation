# services/analysis-service/src/pdrd_analysis_service/infrastructure/ollama.py

"""Structured VLM adapter shared Ollama."""

import base64
import json
import logging
from typing import Any

import httpx

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


class OllamaStructuredVisionModel:
    """Structured Qwen3-VL provider через Ollama."""

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
    ) -> None:
        """Сохраняет runtime параметры Ollama."""
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
        """Вызывает Ollama и возвращает полный JSON."""
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
                message["images"] = [encoded_image]

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
                        connect=(self._connect_timeout_seconds),
                    ),
                ) as client:
                    response = await client.post(
                        (f"{self._base_url}/api/chat"),
                        json={
                            "model": self._model,
                            "messages": [message],
                            "stream": False,
                            "think": False,
                            "format": schema,
                            "keep_alive": (self._keep_alive),
                            "options": {
                                "temperature": 0.0,
                                "seed": (seed + attempt),
                                "repeat_penalty": 1.10,
                                "num_ctx": (self._num_ctx),
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
                done_reason=(
                    response_payload.get(
                        "done_reason",
                    )
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
                prompt_eval_count=(
                    response_payload.get(
                        "prompt_eval_count",
                    )
                ),
                eval_count=(
                    response_payload.get(
                        "eval_count",
                    )
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
                if attempt < self._max_retries:
                    continue

                raise VisionModelError(
                    "Модель не смогла сформировать "
                    "корректный JSON "
                    f"на этапе {stage}. "
                    f"response="
                    f"{last_content[:1800]}",
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

    async def is_ready(self) -> bool:
        """Проверяет наличие configured VLM."""
        try:
            async with httpx.AsyncClient(
                timeout=self._health_timeout_seconds,
            ) as client:
                response = await client.get(f"{self._base_url}/api/tags")

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
