# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/embedding/ollama_release.py

"""Best-effort освобождение loaded Ollama models перед T-indexing."""

import logging

import httpx

LOGGER = logging.getLogger(
    __name__,
)


class OllamaLoadedModelReleaser:
    """Выгружает только реально running Ollama models."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
    ) -> None:
        """Сохраняет HTTP settings."""
        self._base_url = base_url.rstrip(
            "/",
        )

        self._timeout_seconds = timeout_seconds

    async def release_loaded(
        self,
        *,
        model_names: tuple[
            str,
            ...,
        ],
    ) -> None:
        """Best-effort unload указанных models."""
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
            ) as client:
                response = await client.get(
                    f"{self._base_url}/api/ps",
                )

                response.raise_for_status()

                payload = response.json()

                models = payload.get(
                    "models",
                    [],
                )

                running = {
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

                for model_name in model_names:
                    if model_name not in running:
                        continue

                    unload = await client.post(
                        f"{self._base_url}/api/generate",
                        json={
                            "model": model_name,
                            "prompt": "",
                            "stream": False,
                            "keep_alive": 0,
                        },
                    )

                    unload.raise_for_status()

                    LOGGER.info(
                        "ollama_model_released model=%s",
                        model_name,
                    )

        except httpx.HTTPError as error:
            LOGGER.warning(
                "ollama_model_release_skipped error=%s",
                error,
            )
