# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/embedding/ollama.py

"""Ollama embedding adapter."""

import httpx

from pdrd_knowledge_service.application.ports.embedding import (
    EmbeddingProviderError,
)


class OllamaEmbeddingProvider:
    """Строит embeddings через shared Ollama."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        request_timeout_seconds: float,
        connect_timeout_seconds: float,
        health_timeout_seconds: float,
    ) -> None:
        """Сохраняет параметры Ollama adapter."""
        self._base_url = base_url.rstrip(
            "/",
        )

        self._model = model

        self._request_timeout_seconds = request_timeout_seconds

        self._connect_timeout_seconds = connect_timeout_seconds

        self._health_timeout_seconds = health_timeout_seconds

    async def embed(
        self,
        texts: tuple[str, ...],
        *,
        instruction: str | None,
    ) -> list[list[float]]:
        """Строит document или instruction-aware embeddings."""
        if not texts:
            return []

        if instruction is None:
            prepared = [text.strip() for text in texts]
        else:
            prepared = [
                (f"Instruct: {instruction}\nQuery: {text.strip()}") for text in texts
            ]

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    self._request_timeout_seconds,
                    connect=(self._connect_timeout_seconds),
                ),
            ) as client:
                response = await client.post(
                    (f"{self._base_url}/api/embed"),
                    json={
                        "model": self._model,
                        "input": prepared,
                        "truncate": True,
                    },
                )

                response.raise_for_status()

        except httpx.HTTPStatusError as error:
            raise EmbeddingProviderError(
                "Ollama вернул ошибку "
                "при построении embeddings: "
                f"{error.response.status_code}: "
                f"{error.response.text[:1000]}",
            ) from error

        except httpx.HTTPError as error:
            raise EmbeddingProviderError(
                f"Не удалось обратиться к Ollama embeddings: {error}",
            ) from error

        embeddings = response.json().get(
            "embeddings",
        )

        if not isinstance(
            embeddings,
            list,
        ):
            raise EmbeddingProviderError(
                "Ollama вернул некорректный список embeddings.",
            )

        if len(
            embeddings,
        ) != len(
            prepared,
        ):
            raise EmbeddingProviderError(
                "Количество embeddings не совпадает с количеством текстов.",
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
                raise EmbeddingProviderError(
                    "Ollama вернул пустой или некорректный embedding.",
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
                raise EmbeddingProviderError(
                    "Embedding содержит нечисловые значения.",
                ) from error

        return result

    async def is_ready(
        self,
    ) -> bool:
        """Проверяет наличие требуемой Ollama-модели."""
        try:
            async with httpx.AsyncClient(
                timeout=(self._health_timeout_seconds),
            ) as client:
                response = await client.get(
                    (f"{self._base_url}/api/tags"),
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
                model.get(
                    "name",
                    "",
                )
            )
            for model in models
            if isinstance(
                model,
                dict,
            )
        }

        return self._model in names
