# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/embedding/multimodal_http.py

"""HTTP adapter dedicated unified embedding service."""

import base64
from typing import Any

import httpx

from pdrd_knowledge_service.application.ports.multimodal_embedding import (
    MultimodalEmbeddingInput,
    MultimodalEmbeddingProviderError,
    MultimodalEmbeddingTemporaryError,
)


class HttpMultimodalEmbeddingProvider:
    """Вызывает unified Qwen3-VL-Embedding service."""

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
        """Сериализует inputs в service batch-size=1."""
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
                f"Unified embedding service временно недоступен: {error}",
            ) from error

        return result

    @staticmethod
    def _payload_input(
        item: MultimodalEmbeddingInput,
    ) -> dict[str, object]:
        """Строит one-item HTTP payload."""
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
        """Проверяет one-item response."""
        if (
            response.status_code
            in {
                429,
                503,
            }
            or response.status_code >= 500
        ):
            raise MultimodalEmbeddingTemporaryError(
                "Unified embedding service временно "
                "не может выполнить запрос: "
                f"{response.status_code}: "
                f"{response.text[:1000]}",
            )

        if response.status_code >= 400:
            raise MultimodalEmbeddingProviderError(
                "Unified embedding service отклонил "
                f"запрос: {response.status_code}: "
                f"{response.text[:1000]}",
            )

        try:
            payload: Any = response.json()

        except ValueError as error:
            raise MultimodalEmbeddingProviderError(
                "Embedding service вернул некорректный JSON.",
            ) from error

        if not isinstance(
            payload,
            dict,
        ):
            raise MultimodalEmbeddingProviderError(
                "Embedding service вернул invalid payload.",
            )

        embeddings = payload.get(
            "embeddings",
        )

        if (
            not isinstance(
                embeddings,
                list,
            )
            or len(
                embeddings,
            )
            != 1
        ):
            raise MultimodalEmbeddingProviderError(
                "One-item request должен вернуть ровно один embedding.",
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
                "Embedding пуст.",
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
                "Embedding содержит нечисловые значения.",
            ) from error

    async def release(
        self,
    ) -> None:
        """Выгружает checkpoint с normal request timeout."""
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    self._request_timeout_seconds,
                    connect=self._connect_timeout_seconds,
                ),
            ) as client:
                response = await client.post(
                    (f"{self._base_url}/internal/v1/release"),
                )

                response.raise_for_status()

        except httpx.HTTPError as error:
            raise MultimodalEmbeddingTemporaryError(
                "Не удалось выгрузить unified embedding checkpoint.",
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
