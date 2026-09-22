# services/analysis-service/src/pdrd_analysis_service/infrastructure/vllm.py

"""OpenAI-compatible adapter shared vLLM."""

import asyncio
import base64
import json
import logging
import re
import time
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

_RETRYABLE_HTTP_STATUSES = frozenset(
    {
        408,
        425,
        429,
        500,
        502,
        503,
        504,
    }
)

_SCHEMA_NAME_PATTERN = re.compile(
    r"[^A-Za-z0-9_-]+",
)


class VllmStructuredVisionModel:
    """Structured VLM client для shared resident vLLM."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        request_timeout_seconds: float,
        connect_timeout_seconds: float,
        health_timeout_seconds: float,
        max_attempts: int,
        retry_backoff_seconds: float,
        max_retry_num_predict: int,
    ) -> None:
        """Сохраняет stable logical vLLM contract."""
        self._base_url = base_url.rstrip(
            "/",
        )

        self._model = model

        self._request_timeout_seconds = request_timeout_seconds

        self._connect_timeout_seconds = connect_timeout_seconds

        self._health_timeout_seconds = health_timeout_seconds

        self._max_attempts = max_attempts

        self._retry_backoff_seconds = retry_backoff_seconds

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
        """Вызывает OpenAI-compatible structured chat completion."""
        attempt_num_predict = min(
            num_predict,
            self._max_retry_num_predict,
        )

        last_content = ""

        for attempt in range(
            1,
            self._max_attempts + 1,
        ):
            attempt_prompt = self._attempt_prompt(
                prompt=prompt,
                attempt=attempt,
            )

            request_payload = self._request_payload(
                prompt=attempt_prompt,
                schema=schema,
                num_predict=attempt_num_predict,
                seed=seed + attempt,
                stage=stage,
                image_bytes=image_bytes,
            )

            logger.info(
                ("[VLM:%s] START provider=vllm attempt=%s max_tokens=%s image=%s"),
                stage,
                attempt,
                attempt_num_predict,
                image_bytes is not None,
            )

            started_at = time.perf_counter()

            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(
                        self._request_timeout_seconds,
                        connect=(self._connect_timeout_seconds),
                    ),
                ) as client:
                    response = await client.post(
                        (f"{self._base_url}/chat/completions"),
                        json=request_payload,
                    )

                    response.raise_for_status()

            except httpx.HTTPStatusError as error:
                if self._can_retry_transport_error(
                    attempt=attempt,
                    status_code=(error.response.status_code),
                ):
                    logger.warning(
                        (
                            "[VLM:%s] RETRY "
                            "provider=vllm attempt=%s "
                            "reason=http_status status=%s"
                        ),
                        stage,
                        attempt,
                        error.response.status_code,
                    )

                    await self._retry_delay(
                        attempt=attempt,
                    )

                    continue

                raise VisionModelError(
                    (
                        "shared-vlm вернул HTTP ошибку "
                        f"на этапе {stage}: "
                        f"{error.response.status_code}: "
                        f"{error.response.text[:1500]}"
                    ),
                ) from error

            except (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.RemoteProtocolError,
            ) as error:
                if attempt < self._max_attempts:
                    logger.warning(
                        (
                            "[VLM:%s] RETRY "
                            "provider=vllm attempt=%s "
                            "reason=transport "
                            "error_type=%s"
                        ),
                        stage,
                        attempt,
                        type(
                            error,
                        ).__name__,
                    )

                    await self._retry_delay(
                        attempt=attempt,
                    )

                    continue

                raise VisionModelError(
                    (f"Не удалось обратиться к shared-vlm на этапе {stage}: {error}"),
                ) from error

            except httpx.HTTPError as error:
                raise VisionModelError(
                    (f"Не удалось обратиться к shared-vlm на этапе {stage}: {error}"),
                ) from error

            total_duration_ms = round(
                (time.perf_counter() - started_at) * 1000,
                2,
            )

            try:
                response_payload = response.json()

            except ValueError as error:
                if attempt < self._max_attempts:
                    logger.warning(
                        (
                            "[VLM:%s] RETRY "
                            "provider=vllm attempt=%s "
                            "reason=invalid_http_json"
                        ),
                        stage,
                        attempt,
                    )

                    await self._retry_delay(
                        attempt=attempt,
                    )

                    continue

                raise VisionModelError(
                    (f"shared-vlm вернул не-JSON HTTP-ответ на этапе {stage}."),
                ) from error

            try:
                (
                    last_content,
                    finish_reason,
                    thinking,
                    prompt_tokens,
                    completion_tokens,
                ) = self._parse_completion(
                    response_payload,
                )

            except ValueError as error:
                if attempt < self._max_attempts:
                    logger.warning(
                        (
                            "[VLM:%s] RETRY "
                            "provider=vllm attempt=%s "
                            "reason="
                            "invalid_completion_payload"
                        ),
                        stage,
                        attempt,
                    )

                    await self._retry_delay(
                        attempt=attempt,
                    )

                    continue

                raise VisionModelError(
                    (
                        "shared-vlm вернул "
                        "некорректный completion payload "
                        f"на этапе {stage}: {error}"
                    ),
                ) from error

            metrics = GenerationMetrics(
                attempt=attempt,
                done_reason=finish_reason,
                requested_num_predict=(attempt_num_predict),
                total_duration_ms=(total_duration_ms),
                load_duration_ms=0.0,
                prompt_eval_count=(prompt_tokens),
                eval_count=(completion_tokens),
                content_length=len(
                    last_content,
                ),
                thinking_length=len(
                    thinking,
                ),
            )

            logger.info(
                (
                    "[VLM:%s] DONE "
                    "provider=vllm attempt=%s "
                    "reason=%s prompt_tokens=%s "
                    "output_tokens=%s "
                    "content_chars=%s "
                    "thinking_chars=%s "
                    "total_ms=%s"
                ),
                stage,
                attempt,
                metrics.done_reason,
                metrics.prompt_eval_count,
                metrics.eval_count,
                metrics.content_length,
                metrics.thinking_length,
                metrics.total_duration_ms,
            )

            try:
                parsed = json.loads(
                    last_content,
                )

            except json.JSONDecodeError as error:
                if finish_reason == "length":
                    next_num_predict = min(
                        attempt_num_predict * 2,
                        self._max_retry_num_predict,
                    )

                    if (
                        attempt >= self._max_attempts
                        or next_num_predict <= attempt_num_predict
                    ):
                        raise VisionModelError(
                            (
                                "shared-vlm исчерпал "
                                "output budget "
                                f"на этапе {stage}: "
                                "max_tokens="
                                f"{attempt_num_predict}, "
                                "prompt_tokens="
                                f"{prompt_tokens}, "
                                "output_tokens="
                                f"{completion_tokens}. "
                                "Повтор с тем же output "
                                "budget запрещён."
                            ),
                        ) from error

                    logger.warning(
                        (
                            "[VLM:%s] RETRY "
                            "provider=vllm attempt=%s "
                            "reason=output_limit "
                            "current_max_tokens=%s "
                            "next_max_tokens=%s"
                        ),
                        stage,
                        attempt,
                        attempt_num_predict,
                        next_num_predict,
                    )

                    attempt_num_predict = next_num_predict

                    await self._retry_delay(
                        attempt=attempt,
                    )

                    continue

                if attempt < self._max_attempts:
                    logger.warning(
                        (
                            "[VLM:%s] RETRY "
                            "provider=vllm attempt=%s "
                            "reason=malformed_json"
                        ),
                        stage,
                        attempt,
                    )

                    await self._retry_delay(
                        attempt=attempt,
                    )

                    continue

                raise VisionModelError(
                    (
                        "shared-vlm не сформировал "
                        "корректный JSON "
                        f"на этапе {stage}. "
                        "response="
                        f"{last_content[:1800]}"
                    ),
                ) from error

            if not isinstance(
                parsed,
                dict,
            ):
                raise VisionModelError(
                    ("Structured shared-vlm вернул JSON не объектного типа."),
                )

            return GenerationResult(
                payload=parsed,
                metrics=metrics,
            )

        raise VisionModelError(
            (f"Не удалось получить structured JSON от shared-vlm на этапе {stage}."),
        )

    async def is_ready(
        self,
    ) -> bool:
        """Проверяет health и наличие stable logical model alias."""
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    self._health_timeout_seconds,
                    connect=(self._health_timeout_seconds),
                ),
            ) as client:
                health_response = await client.get(
                    self._health_url(),
                )

                health_response.raise_for_status()

                models_response = await client.get(
                    (f"{self._base_url}/models"),
                )

                models_response.raise_for_status()

        except httpx.HTTPError:
            return False

        try:
            payload = models_response.json()

        except ValueError:
            return False

        models = payload.get(
            "data",
            [],
        )

        if not isinstance(
            models,
            list,
        ):
            return False

        return any(
            (
                isinstance(
                    item,
                    dict,
                )
                and str(
                    item.get(
                        "id",
                        "",
                    )
                )
                == self._model
            )
            for item in models
        )

    def _request_payload(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        num_predict: int,
        seed: int,
        stage: str,
        image_bytes: bytes | None,
    ) -> dict[str, Any]:
        """Строит OpenAI-compatible request без physical GPU details."""
        content: (
            str
            | list[
                dict[
                    str,
                    Any,
                ]
            ]
        )

        if image_bytes is None:
            content = prompt

        else:
            content = [
                {
                    "type": "text",
                    "text": prompt,
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": (
                            self._image_data_url(
                                image_bytes,
                            )
                        ),
                    },
                },
            ]

        return {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": content,
                }
            ],
            "temperature": 0.0,
            "seed": seed,
            "max_tokens": num_predict,
            "stream": False,
            "chat_template_kwargs": {
                "enable_thinking": False,
            },
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": (
                        self._schema_name(
                            stage,
                        )
                    ),
                    "strict": True,
                    "schema": schema,
                },
            },
        }

    @staticmethod
    def _attempt_prompt(
        *,
        prompt: str,
        attempt: int,
    ) -> str:
        """Добавляет corrective instruction только для retry."""
        if attempt <= 1:
            return prompt

        return (
            prompt + "\n\nПРЕДЫДУЩИЙ ОТВЕТ "
            "БЫЛ ОБРЕЗАН ИЛИ "
            "НЕ ЯВЛЯЛСЯ ПОЛНЫМ JSON. "
            "Ответь существенно короче. "
            "Не уменьшай количество найденных "
            "candidate findings. "
            "Не повторяй рассуждения. "
            "Верни только полный JSON."
        )

    @staticmethod
    def _parse_completion(
        payload: Any,
    ) -> tuple[
        str,
        str | None,
        str,
        int | None,
        int | None,
    ]:
        """Извлекает content и usage из OpenAI-compatible response."""
        if not isinstance(
            payload,
            dict,
        ):
            raise ValueError(
                ("response payload должен быть object"),
            )

        choices = payload.get(
            "choices",
        )

        if (
            not isinstance(
                choices,
                list,
            )
            or not choices
        ):
            raise ValueError(
                ("response не содержит choices"),
            )

        choice = choices[0]

        if not isinstance(
            choice,
            dict,
        ):
            raise ValueError(
                "choice должен быть object",
            )

        message = choice.get(
            "message",
        )

        if not isinstance(
            message,
            dict,
        ):
            raise ValueError(
                ("choice.message должен быть object"),
            )

        content = message.get(
            "content",
        )

        if not isinstance(
            content,
            str,
        ):
            raise ValueError(
                ("choice.message.content должен быть string"),
            )

        reasoning = message.get(
            "reasoning",
        )

        thinking = (
            reasoning
            if isinstance(
                reasoning,
                str,
            )
            else ""
        )

        usage = payload.get(
            "usage",
        )

        if not isinstance(
            usage,
            dict,
        ):
            usage = {}

        return (
            content,
            (
                str(
                    choice.get(
                        "finish_reason",
                    )
                )
                if choice.get(
                    "finish_reason",
                )
                is not None
                else None
            ),
            thinking,
            VllmStructuredVisionModel._optional_int(
                usage.get(
                    "prompt_tokens",
                )
            ),
            VllmStructuredVisionModel._optional_int(
                usage.get(
                    "completion_tokens",
                )
            ),
        )

    def _can_retry_transport_error(
        self,
        *,
        attempt: int,
        status_code: int,
    ) -> bool:
        """Разрешает bounded retry только transient HTTP ошибок."""
        return attempt < self._max_attempts and status_code in _RETRYABLE_HTTP_STATUSES

    async def _retry_delay(
        self,
        *,
        attempt: int,
    ) -> None:
        """Даёт shared queue короткое bounded окно для освобождения."""
        await asyncio.sleep(
            (self._retry_backoff_seconds * attempt),
        )

    def _health_url(
        self,
    ) -> str:
        """Строит root /health из stable /v1 endpoint."""
        if self._base_url.endswith(
            "/v1",
        ):
            return self._base_url[:-3] + "/health"

        return self._base_url + "/health"

    @staticmethod
    def _schema_name(
        stage: str,
    ) -> str:
        """Формирует допустимое имя response_format schema."""
        suffix = _SCHEMA_NAME_PATTERN.sub(
            "_",
            stage,
        ).strip(
            "_",
        )

        if not suffix:
            suffix = "generation"

        return (f"pdrd_{suffix}")[:64]

    @staticmethod
    def _image_data_url(
        image_bytes: bytes,
    ) -> str:
        """Кодирует supported image как data URI."""
        mime_type = "image/png"

        if image_bytes.startswith(
            b"\xff\xd8\xff",
        ):
            mime_type = "image/jpeg"

        elif (
            image_bytes.startswith(
                b"RIFF",
            )
            and image_bytes[8:12] == b"WEBP"
        ):
            mime_type = "image/webp"

        encoded = base64.b64encode(
            image_bytes,
        ).decode(
            "ascii",
        )

        return f"data:{mime_type};base64,{encoded}"

    @staticmethod
    def _optional_int(
        value: Any,
    ) -> int | None:
        """Нормализует token counters."""
        if isinstance(
            value,
            bool,
        ):
            return None

        try:
            return int(
                value,
            )

        except (
            TypeError,
            ValueError,
        ):
            return None
