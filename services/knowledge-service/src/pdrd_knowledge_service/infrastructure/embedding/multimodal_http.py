# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/embedding/multimodal_http.py

"""HTTP adapter dedicated multimodal embedding service."""

import base64
from typing import Any

import httpx

from pdrd_knowledge_service.application.ports.multimodal_embedding import (
    MultimodalEmbeddingInput,
    MultimodalEmbeddingProviderError,
    MultimodalEmbeddingTemporaryError,
)


class HttpMultimodalEmbeddingProvider:
    """Вызывает internal Qwen3-VL-Embedding service."""

    def __init__(
        self,
        *,
        base_url: str,
        request_timeout_seconds: float,
        connect_timeout_seconds: float,
        health_timeout_seconds: float,
    ) -> None:
        """Сохраняет HTTP settings."""
        self._base_url = base_url.rstrip(
            "/",
        )

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
        """Строит embeddings последовательными bounded HTTP-запросами."""
        if not inputs:
            return []

        result: list[list[float]] = []

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    self._request_timeout_seconds,
                    connect=self._connect_timeout_seconds,
                ),
            ) as client:
                for item in inputs:
                    response = await client.post(
                        (f"{self._base_url}/internal/v1/embeddings"),
                        json={
                            "inputs": [
                                self._payload_input(
                                    item,
                                )
                            ],
                        },
                    )

                    result.append(
                        self._parse_single_embedding(
                            response,
                        )
                    )

        except httpx.HTTPError as error:
            raise MultimodalEmbeddingTemporaryError(
                f"Multimodal embedding service временно недоступен: {error}",
            ) from error

        return result

    @staticmethod
    def _payload_input(
        item: MultimodalEmbeddingInput,
    ) -> dict[
        str,
        object,
    ]:
        """Преобразует один domain input в bounded HTTP payload."""
        return {
            "text": item.text,
            "image_base64": (
                base64.b64encode(
                    item.image_bytes,
                ).decode(
                    "ascii",
                )
                if item.image_bytes is not None
                else None
            ),
            "instruction": item.instruction,
        }

    @staticmethod
    def _parse_single_embedding(
        response: httpx.Response,
    ) -> list[float]:
        """Проверяет ответ одного bounded embedding request."""
        if (
            response.status_code
            in {
                429,
                503,
            }
            or response.status_code >= 500
        ):
            raise MultimodalEmbeddingTemporaryError(
                "Multimodal embedding service временно "
                "не может выполнить запрос: "
                f"{response.status_code}: "
                f"{response.text[:1000]}",
            )

        if response.status_code >= 400:
            raise MultimodalEmbeddingProviderError(
                "Multimodal embedding service отклонил "
                f"запрос: {response.status_code}: "
                f"{response.text[:1000]}",
            )

        try:
            payload: Any = response.json()

        except ValueError as error:
            raise MultimodalEmbeddingProviderError(
                "Multimodal service вернул некорректный JSON.",
            ) from error

        if not isinstance(
            payload,
            dict,
        ):
            raise MultimodalEmbeddingProviderError(
                "Multimodal service вернул некорректный payload.",
            )

        embeddings = payload.get(
            "embeddings",
        )

        if not isinstance(
            embeddings,
            list,
        ):
            raise MultimodalEmbeddingProviderError(
                "Multimodal service вернул некорректный embeddings payload.",
            )

        if (
            len(
                embeddings,
            )
            != 1
        ):
            raise MultimodalEmbeddingProviderError(
                "Bounded multimodal request должен вернуть ровно один embedding.",
            )

        vector = embeddings[0]

        if (
            not isinstance(
                vector,
                list,
            )
            or not vector
        ):
            raise MultimodalEmbeddingProviderError(
                "Multimodal embedding пуст.",
            )

        try:
            return [
                float(
                    value,
                )
                for value in vector
            ]

        except (
            TypeError,
            ValueError,
        ) as error:
            raise MultimodalEmbeddingProviderError(
                "Multimodal embedding содержит нечисловые значения.",
            ) from error

    async def release(
        self,
    ) -> None:
        """Явно выгружает checkpoint."""
        try:
            async with httpx.AsyncClient(
                timeout=self._health_timeout_seconds,
            ) as client:
                response = await client.post(
                    (f"{self._base_url}/internal/v1/release"),
                )

                response.raise_for_status()

        except httpx.HTTPError as error:
            raise MultimodalEmbeddingTemporaryError(
                "Не удалось выгрузить multimodal checkpoint.",
            ) from error

    async def is_ready(
        self,
    ) -> bool:
        """Проверяет CUDA readiness без model load."""
        try:
            async with httpx.AsyncClient(
                timeout=self._health_timeout_seconds,
            ) as client:
                response = await client.get(
                    f"{self._base_url}/health/ready",
                )

            return response.status_code == 200

        except httpx.HTTPError:
            return False
