# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/embedding/multimodal_http.py

"""HTTP adapter dedicated multimodal embedding service."""

import base64

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
        """Строит embeddings через internal HTTP API."""
        payload_inputs: list[
            dict[
                str,
                object,
            ]
        ] = []

        for item in inputs:
            payload_inputs.append(
                {
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
            )

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    self._request_timeout_seconds,
                    connect=(self._connect_timeout_seconds),
                ),
            ) as client:
                response = await client.post(
                    (f"{self._base_url}/internal/v1/embeddings"),
                    json={
                        "inputs": payload_inputs,
                    },
                )

        except httpx.HTTPError as error:
            raise MultimodalEmbeddingTemporaryError(
                f"Multimodal embedding service временно недоступен: {error}",
            ) from error

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

        try:
            response.raise_for_status()

        except httpx.HTTPStatusError as error:
            raise MultimodalEmbeddingProviderError(
                "Multimodal embedding service отклонил "
                f"запрос: {response.status_code}: "
                f"{response.text[:1000]}",
            ) from error

        payload = response.json()

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

        if len(
            embeddings,
        ) != len(
            inputs,
        ):
            raise MultimodalEmbeddingProviderError(
                "Количество multimodal embeddings не совпало с batch.",
            )

        result: list[list[float]] = []

        for vector in embeddings:
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
                result.append(
                    [
                        float(
                            value,
                        )
                        for value in vector
                    ]
                )

            except (
                TypeError,
                ValueError,
            ) as error:
                raise MultimodalEmbeddingProviderError(
                    "Multimodal embedding содержит нечисловые значения.",
                ) from error

        return result

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
