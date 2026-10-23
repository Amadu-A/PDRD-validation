# services/analysis-service/src/pdrd_analysis_service/infrastructure/vllm.py

"""OpenAI-compatible adapter shared vLLM."""

import asyncio
import base64
import json
import logging
import re
import time
from dataclasses import replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from pdrd_analysis_service.application.ports.vision_model import (
    VisionModelError,
    VisionModelOutputBudgetError,
)
from pdrd_analysis_service.application.vision_execution import current_vision_execution
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


def completed_specification_fields(content: str) -> dict:
    """Читает лишь целиком завершённые поля верхнего объекта без ремонта JSON.

    Незавершённая строка, массив или объект не дополняется. Результат остаётся
    неполным чтением; строки далее проходят обычную предметную валидацию.
    """
    decoder, result, seen = json.JSONDecoder(), {}, set()
    text = content.lstrip()
    if not text.startswith("{"):
        return {}
    offset = 1
    while offset < len(text):
        offset += len(text[offset:]) - len(text[offset:].lstrip())
        try:
            key, end = decoder.raw_decode(text, offset)
            if not isinstance(key, str) or key in seen:
                return {}
            seen.add(key)
            offset = end + len(text[end:]) - len(text[end:].lstrip())
            if offset >= len(text) or text[offset] != ":":
                break
            offset += 1
            offset += len(text[offset:]) - len(text[offset:].lstrip())
            value, end = decoder.raw_decode(text, offset)
        except json.JSONDecodeError:
            break
        if key in {"specification_rows", "specification_notes"} and isinstance(
            value, list
        ):
            result[key] = value
        offset = end + len(text[end:]) - len(text[end:].lstrip())
        if offset >= len(text) or text[offset] != ",":
            break
        offset += 1
    return result


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
        self._max_model_len: int | None = None

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
        attempt_metrics: list[GenerationMetrics] = []

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
                (
                    "[VLM:%s] START provider=vllm attempt=%s max_tokens=%s image=%s prompt_chars=%s request_bytes=%s"
                ),
                stage,
                attempt,
                attempt_num_predict,
                image_bytes is not None,
                len(attempt_prompt),
                len(
                    json.dumps(
                        request_payload, ensure_ascii=False, separators=(",", ":")
                    ).encode("utf-8")
                ),
            )

            started_at = time.perf_counter()

            def record_failure(
                reason: str,
                attempt=attempt,
                budget=attempt_num_predict,
                started=started_at,
            ) -> GenerationMetrics:
                """Учитывает время и запрос; неизвестный расход GPU остаётся неизвестным."""
                attempt_metrics.append(
                    GenerationMetrics(
                        attempt,
                        reason,
                        budget,
                        round((time.perf_counter() - started) * 1000, 2),
                        0.0,
                        None,
                        None,
                        0,
                        0,
                    )
                )
                return self._combined_metrics(attempt_metrics)

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
                failure_metrics = record_failure("http_status")
                code, retry_after, request_id = self._provider_failure(error.response)
                retryable = (
                    error.response.status_code in _RETRYABLE_HTTP_STATUSES
                    and code
                    not in {
                        "context_length_exceeded",
                        "invalid_request_error",
                        "invalid_request",
                    }
                )
                logger.warning(
                    "[VLM:%s] HTTP_ERROR status=%s code=%s request_id=%s retry_after=%.2f",
                    stage,
                    error.response.status_code,
                    code or "unknown",
                    request_id or "unknown",
                    retry_after,
                )
                if retryable and self._can_retry_transport_error(
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
                        retry_after_seconds=retry_after,
                    )

                    continue

                raise VisionModelError(
                    (
                        "shared-vlm вернул HTTP ошибку "
                        f"на этапе {stage}: "
                        f"{error.response.status_code}."
                        + (f" Код провайдера: {code}." if code else "")
                    ),
                    metrics=failure_metrics,
                    status_code=error.response.status_code,
                    error_code=code,
                    retry_after_seconds=retry_after,
                ) from error

            except (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.RemoteProtocolError,
            ) as error:
                failure_metrics = record_failure("transport")
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
                    (
                        f"Не удалось обратиться к shared-vlm на этапе {stage}: {type(error).__name__}"
                    ),
                    metrics=failure_metrics,
                ) from error

            except httpx.HTTPError as error:
                failure_metrics = record_failure("transport")
                raise VisionModelError(
                    (
                        f"Не удалось обратиться к shared-vlm на этапе {stage}: {type(error).__name__}"
                    ),
                    metrics=failure_metrics,
                ) from error

            total_duration_ms = round(
                (time.perf_counter() - started_at) * 1000,
                2,
            )

            try:
                response_payload = response.json()

            except ValueError as error:
                failure_metrics = record_failure("invalid_http_json")
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
                    metrics=failure_metrics,
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
                failure_metrics = record_failure("invalid_completion_payload")
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
                        f"на этапе {stage}."
                    ),
                    metrics=failure_metrics,
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
            attempt_metrics.append(metrics)

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
                if finish_reason == "length":
                    raise json.JSONDecodeError(
                        "Ответ достиг предела вывода", last_content, len(last_content)
                    )
                parsed = json.loads(
                    last_content,
                )

            except json.JSONDecodeError as error:
                if finish_reason == "length":
                    retry_limit = self._max_retry_num_predict
                    if self._max_model_len is not None and prompt_tokens is not None:
                        retry_limit = min(
                            retry_limit,
                            max(0, self._max_model_len - prompt_tokens - 512),
                        )
                    next_num_predict = min(
                        attempt_num_predict * 2,
                        retry_limit,
                    )

                    if (
                        attempt >= self._max_attempts
                        or next_num_predict <= attempt_num_predict
                        or (
                            stage.startswith("page_understanding:")
                            and ":recovery:" not in stage
                            and "document_facts" in schema.get("properties", {})
                            and "equipment_identities" in schema.get("properties", {})
                        )
                    ):
                        raise VisionModelOutputBudgetError(
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
                            metrics=self._combined_metrics(attempt_metrics),
                            completed_fields=completed_specification_fields(
                                last_content
                            )
                            if stage.startswith("page_understanding:")
                            and "specification_rows" in schema.get("properties", {})
                            else None,
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
                    (f"shared-vlm не сформировал корректный JSON на этапе {stage}. "),
                    metrics=self._combined_metrics(attempt_metrics),
                ) from error

            if not isinstance(
                parsed,
                dict,
            ):
                raise VisionModelError(
                    ("Structured shared-vlm вернул JSON не объектного типа."),
                    metrics=self._combined_metrics(attempt_metrics),
                )

            return GenerationResult(
                payload=parsed,
                metrics=self._combined_metrics(attempt_metrics),
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

        self._max_model_len = None
        models = payload.get(
            "data",
            [],
        )

        if not isinstance(
            models,
            list,
        ):
            return False

        for item in models:
            if isinstance(item, dict) and str(item.get("id", "")) == self._model:
                length = self._optional_int(item.get("max_model_len"))
                self._max_model_len = (
                    length if length is not None and length > 0 else None
                )
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

    @staticmethod
    def _combined_metrics(attempts: list[GenerationMetrics]) -> GenerationMetrics:
        """Считает затраты всех полученных ответов, включая неудачные JSON/length попытки."""

        def total(field):
            """Не подменяет неизвестный token usage нулём."""
            values = [getattr(item, field) for item in attempts]
            return None if any(value is None for value in values) else sum(values)

        return replace(
            attempts[-1],
            requested_num_predict=total("requested_num_predict"),
            total_duration_ms=total("total_duration_ms"),
            load_duration_ms=total("load_duration_ms"),
            prompt_eval_count=total("prompt_eval_count"),
            eval_count=total("eval_count"),
            content_length=total("content_length"),
            thinking_length=total("thinking_length"),
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
        retry_after_seconds: float = 0,
    ) -> None:
        """Даёт shared queue короткое bounded окно для освобождения."""
        remaining = min(
            60.0, max(self._retry_backoff_seconds * attempt, retry_after_seconds)
        )
        while remaining > 0:
            execution = current_vision_execution.get()
            if execution is not None:
                await execution.checkpoint()
            interval = min(1.0, remaining)
            await asyncio.sleep(interval)
            remaining -= interval
        execution = current_vision_execution.get()
        if execution is not None:
            await execution.checkpoint()

    @staticmethod
    def _provider_failure(response: httpx.Response) -> tuple[str, float, str]:
        """Читает только безопасный код и служебные заголовки, не публикуя сообщение с документом."""
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        error = payload.get("error") if isinstance(payload, dict) else None
        code = (
            str(error.get("code") or error.get("type") or "")
            if isinstance(error, dict)
            else ""
        )
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", code):
            code = ""
        request_id = response.headers.get("x-request-id", "")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", request_id):
            request_id = ""
        raw = response.headers.get("Retry-After", "")
        try:
            retry_after = float(raw)
        except ValueError:
            try:
                retry_after = (
                    parsedate_to_datetime(raw) - datetime.now(UTC)
                ).total_seconds()
            except (ValueError, TypeError, OverflowError):
                retry_after = 0
        return code, max(0.0, min(60.0, retry_after)), request_id

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
