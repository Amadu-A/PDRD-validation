# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/embedding/multimodal_http.py

"""OpenAI-compatible adapter resident shared multimodal embedding."""

import base64
import logging
import time
from typing import Any

import httpx

from pdrd_knowledge_service.application.ports.multimodal_embedding import (
    MultimodalEmbeddingInput,
    MultimodalEmbeddingProviderError,
    MultimodalEmbeddingTemporaryError,
)

logger = logging.getLogger(
    __name__,
)

_DEFAULT_MODEL = "shared-embedding"

_DEFAULT_OUTPUT_DIMENSION = 4096

_DEFAULT_INSTRUCTION = "Represent the user's input."

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


class HttpMultimodalEmbeddingProvider:
    """Вызывает resident shared Qwen3-VL-Embedding через vLLM."""

    def __init__(
        self,
        *,
        base_url: str,
        request_timeout_seconds: float,
        connect_timeout_seconds: float,
        health_timeout_seconds: float,
        model: str = _DEFAULT_MODEL,
        output_dimension: int = _DEFAULT_OUTPUT_DIMENSION,
    ) -> None:
        """Сохраняет stable logical shared-embedding contract."""
        normalized_base_url = base_url.rstrip(
            "/",
        )

        normalized_model = model.strip()

        if not normalized_base_url:
            raise ValueError(
                "Shared embedding base_url пуст.",
            )

        if not normalized_model:
            raise ValueError(
                "Shared embedding model alias пуст.",
            )

        if output_dimension < 1:
            raise ValueError(
                "Shared embedding output_dimension должен быть положительным.",
            )

        self._base_url = normalized_base_url

        self._model = normalized_model

        self._output_dimension = output_dimension

        self._request_timeout_seconds = request_timeout_seconds

        self._connect_timeout_seconds = connect_timeout_seconds

        self._health_timeout_seconds = health_timeout_seconds

    async def embed(
        self,
        inputs: tuple[
            MultimodalEmbeddingInput,
            ...,
        ],
    ) -> list[list[float]]:
        """Строит один bounded batch через resident shared vLLM."""
        if not inputs:
            return []

        request_payload = self._request_payload(
            inputs,
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
                    (f"{self._base_url}/embeddings"),
                    json=request_payload,
                )

        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ) as error:
            raise MultimodalEmbeddingTemporaryError(
                (
                    "Shared embedding service "
                    "временно недоступен: "
                    f"{type(error).__name__}: {error}"
                ),
            ) from error

        except httpx.HTTPError as error:
            raise MultimodalEmbeddingTemporaryError(
                (f"Не удалось обратиться к shared embedding service: {error}"),
            ) from error

        vectors = self._parse_embeddings(
            response=response,
            expected_count=len(
                inputs,
            ),
        )

        total_ms = round(
            (time.perf_counter() - started_at) * 1000,
            2,
        )

        logger.info(
            (
                "shared_embedding_done "
                "items=%s "
                "multimodal_items=%s "
                "instructed_items=%s "
                "dimension=%s "
                "total_ms=%s"
            ),
            len(
                inputs,
            ),
            sum(1 for item in inputs if item.image_bytes is not None),
            sum(
                1
                for item in inputs
                if (item.instruction is not None and item.instruction.strip())
            ),
            self._output_dimension,
            total_ms,
        )

        return vectors

    async def release(
        self,
    ) -> None:
        """Compatibility no-op для старого application contract.

        Shared vLLM checkpoint является resident и не должен
        выгружаться между запросами.

        Метод сохраняется только до окончательного cutover,
        чтобы Этап 1 не смешивать с изменением T lifecycle.
        """
        return None

    async def is_ready(
        self,
    ) -> bool:
        """Проверяет health и stable logical model alias."""
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

                if health_response.status_code != 200:
                    return False

                models_response = await client.get(
                    (f"{self._base_url}/models"),
                )

                if models_response.status_code != 200:
                    return False

        except httpx.HTTPError:
            return False

        try:
            payload: Any = models_response.json()

        except ValueError:
            return False

        if not isinstance(
            payload,
            dict,
        ):
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
        inputs: tuple[
            MultimodalEmbeddingInput,
            ...,
        ],
    ) -> dict[
        str,
        Any,
    ]:
        """Строит OpenAI-compatible embedding request."""
        conversations = [
            self._conversation(
                item,
            )
            for item in inputs
        ]

        messages: object = (
            conversations[0]
            if len(
                conversations,
            )
            == 1
            else conversations
        )

        return {
            "model": self._model,
            "messages": messages,
            "encoding_format": "float",
            "dimensions": (self._output_dimension),
        }

    @staticmethod
    def _conversation(
        item: MultimodalEmbeddingInput,
    ) -> list[
        dict[
            str,
            Any,
        ]
    ]:
        """Преобразует PDRD input в Qwen chat representation."""
        instruction = (
            item.instruction.strip()
            if (item.instruction is not None and item.instruction.strip())
            else _DEFAULT_INSTRUCTION
        )

        user_content: list[
            dict[
                str,
                Any,
            ]
        ] = []

        if item.image_bytes is not None:
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": (
                            HttpMultimodalEmbeddingProvider._image_data_url(
                                item.image_bytes,
                            )
                        ),
                    },
                }
            )

        if item.text is not None and item.text.strip():
            user_content.append(
                {
                    "type": "text",
                    "text": item.text.strip(),
                }
            )

        return [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": instruction,
                    }
                ],
            },
            {
                "role": "user",
                "content": user_content,
            },
        ]

    def _parse_embeddings(
        self,
        *,
        response: httpx.Response,
        expected_count: int,
    ) -> list[list[float]]:
        """Проверяет OpenAI-compatible embedding response."""
        if response.status_code in _RETRYABLE_HTTP_STATUSES:
            raise MultimodalEmbeddingTemporaryError(
                (
                    "Shared embedding service "
                    "временно не может выполнить запрос: "
                    f"{response.status_code}: "
                    f"{response.text[:1500]}"
                ),
            )

        if response.status_code >= 400:
            raise MultimodalEmbeddingProviderError(
                (
                    "Shared embedding service "
                    "отклонил запрос: "
                    f"{response.status_code}: "
                    f"{response.text[:1500]}"
                ),
            )

        try:
            payload: Any = response.json()

        except ValueError as error:
            raise MultimodalEmbeddingProviderError(
                ("Shared embedding service вернул некорректный JSON."),
            ) from error

        if not isinstance(
            payload,
            dict,
        ):
            raise MultimodalEmbeddingProviderError(
                ("Shared embedding service вернул invalid payload."),
            )

        response_model = payload.get(
            "model",
        )

        if (
            response_model is not None
            and str(
                response_model,
            )
            != self._model
        ):
            raise MultimodalEmbeddingProviderError(
                ("Shared embedding service вернул неожиданный model alias."),
            )

        data = payload.get(
            "data",
        )

        if not isinstance(
            data,
            list,
        ):
            raise MultimodalEmbeddingProviderError(
                ("Shared embedding response не содержит data."),
            )

        if (
            len(
                data,
            )
            != expected_count
        ):
            raise MultimodalEmbeddingProviderError(
                ("Количество shared embeddings не совпадает с количеством inputs."),
            )

        indexed: dict[
            int,
            list[float],
        ] = {}

        for raw_item in data:
            if not isinstance(
                raw_item,
                dict,
            ):
                raise MultimodalEmbeddingProviderError(
                    ("Embedding data item имеет неверный формат."),
                )

            raw_index = raw_item.get(
                "index",
            )

            if isinstance(
                raw_index,
                bool,
            ) or not isinstance(
                raw_index,
                int,
            ):
                raise MultimodalEmbeddingProviderError(
                    ("Embedding data item не содержит integer index."),
                )

            if raw_index in indexed:
                raise MultimodalEmbeddingProviderError(
                    ("Embedding response содержит duplicate index."),
                )

            raw_vector = raw_item.get(
                "embedding",
            )

            if (
                not isinstance(
                    raw_vector,
                    list,
                )
                or not raw_vector
            ):
                raise MultimodalEmbeddingProviderError(
                    "Embedding пуст.",
                )

            try:
                vector = [
                    float(
                        value,
                    )
                    for value in raw_vector
                ]

            except (
                TypeError,
                ValueError,
            ) as error:
                raise MultimodalEmbeddingProviderError(
                    ("Embedding содержит нечисловые значения."),
                ) from error

            if (
                len(
                    vector,
                )
                != self._output_dimension
            ):
                raise MultimodalEmbeddingProviderError(
                    (
                        "Shared embedding имеет "
                        "неправильную dimension: "
                        f"{len(vector)} != "
                        f"{self._output_dimension}."
                    ),
                )

            indexed[raw_index] = vector

        expected_indexes = set(
            range(
                expected_count,
            )
        )

        if (
            set(
                indexed,
            )
            != expected_indexes
        ):
            raise MultimodalEmbeddingProviderError(
                ("Shared embedding response содержит неверный набор indexes."),
            )

        return [
            indexed[index]
            for index in range(
                expected_count,
            )
        ]

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
