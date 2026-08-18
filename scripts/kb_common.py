# scripts/kb_common.py

"""Общие функции для индексации и поиска в базе знаний."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import httpx


POINT_NAMESPACE = UUID(
    "3154088f-e364-4b20-aa51-a06916a22806"
)


def get_repo_root() -> Path:
    """Получить корень репозитория."""

    return Path(__file__).resolve().parent.parent


def load_env_file() -> None:
    """Загрузить простые KEY=VALUE из локального .env.

    Существующие переменные окружения имеют приоритет.
    """

    env_path = get_repo_root() / ".env"

    if not env_path.is_file():
        return

    for raw_line in env_path.read_text(
        encoding="utf-8",
    ).splitlines():
        line = raw_line.strip()

        if (
            not line
            or line.startswith("#")
            or "=" not in line
        ):
            continue

        key, value = line.split(
            "=",
            maxsplit=1,
        )

        key = key.strip()
        value = value.strip().strip(
            "\"'"
        )

        os.environ.setdefault(
            key,
            value,
        )


@dataclass(frozen=True)
class KBSettings:
    """Настройки локальной базы знаний."""

    ollama_url: str
    qdrant_url: str

    embedding_model: str

    normative_collection: str
    experience_collection: str

    chunk_size: int
    chunk_overlap: int
    embed_batch_size: int


def get_settings() -> KBSettings:
    """Получить настройки CLI."""

    load_env_file()

    ollama_port = os.getenv(
        "OLLAMA_PORT",
        "11434",
    )

    qdrant_port = os.getenv(
        "QDRANT_HTTP_PORT",
        "6333",
    )

    return KBSettings(
        ollama_url=os.getenv(
            "KB_OLLAMA_URL",
            f"http://localhost:{ollama_port}",
        ).rstrip("/"),
        qdrant_url=os.getenv(
            "KB_QDRANT_URL",
            f"http://localhost:{qdrant_port}",
        ).rstrip("/"),
        embedding_model=os.getenv(
            "OLLAMA_EMBEDDING_MODEL",
            "qwen3-embedding:0.6b",
        ),
        normative_collection=os.getenv(
            "QDRANT_NORMATIVE_COLLECTION",
            "dva_normative",
        ),
        experience_collection=os.getenv(
            "QDRANT_EXPERIENCE_COLLECTION",
            "dva_experience",
        ),
        chunk_size=int(
            os.getenv(
                "KB_CHUNK_SIZE",
                "3500",
            )
        ),
        chunk_overlap=int(
            os.getenv(
                "KB_CHUNK_OVERLAP",
                "500",
            )
        ),
        embed_batch_size=int(
            os.getenv(
                "KB_EMBED_BATCH_SIZE",
                "8",
            )
        ),
    )


class OllamaEmbeddingClient:
    """Клиент Ollama embeddings API."""

    def __init__(
        self,
        base_url: str,
        model: str,
    ) -> None:
        self.base_url = base_url
        self.model = model

    def embed(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        """Построить embeddings для набора текстов."""

        if not texts:
            return []

        with httpx.Client(
            timeout=600.0,
        ) as client:
            response = client.post(
                f"{self.base_url}/api/embed",
                json={
                    "model": self.model,
                    "input": texts,
                    "truncate": True,
                },
            )

            response.raise_for_status()

        payload = response.json()

        embeddings = payload.get(
            "embeddings",
        )

        if not isinstance(
            embeddings,
            list,
        ):
            raise RuntimeError(
                "Ollama не вернул embeddings."
            )

        if len(embeddings) != len(
            texts
        ):
            raise RuntimeError(
                "Количество embeddings "
                "не совпадает с количеством текстов."
            )

        return embeddings


class QdrantRestClient:
    """Минимальный REST-клиент Qdrant."""

    def __init__(
        self,
        base_url: str,
    ) -> None:
        self.base_url = base_url

    def is_alive(self) -> bool:
        """Проверить доступность Qdrant."""

        try:
            with httpx.Client(
                timeout=10.0,
            ) as client:
                response = client.get(
                    f"{self.base_url}/readyz"
                )

            return response.is_success

        except httpx.HTTPError:
            return False

    def collection_exists(
        self,
        collection: str,
    ) -> bool:
        """Проверить существование коллекции."""

        with httpx.Client(
            timeout=20.0,
        ) as client:
            response = client.get(
                f"{self.base_url}"
                f"/collections/{collection}"
            )

        if response.status_code == 404:
            return False

        response.raise_for_status()

        return True

    def create_collection(
        self,
        collection: str,
        vector_size: int,
    ) -> None:
        """Создать коллекцию с cosine distance."""

        with httpx.Client(
            timeout=30.0,
        ) as client:
            response = client.put(
                f"{self.base_url}"
                f"/collections/{collection}",
                json={
                    "vectors": {
                        "size": vector_size,
                        "distance": "Cosine",
                    }
                },
            )

            response.raise_for_status()

    def ensure_collection(
        self,
        collection: str,
        vector_size: int,
    ) -> None:
        """Создать коллекцию, если она отсутствует."""

        if self.collection_exists(
            collection
        ):
            return

        self.create_collection(
            collection,
            vector_size,
        )

    def get_source_payload(
        self,
        collection: str,
        source_key: str,
    ) -> dict[str, Any] | None:
        """Получить payload любой записи данного источника."""

        with httpx.Client(
            timeout=30.0,
        ) as client:
            response = client.post(
                f"{self.base_url}"
                f"/collections/{collection}"
                "/points/scroll",
                json={
                    "filter": {
                        "must": [
                            {
                                "key": "source_key",
                                "match": {
                                    "value": source_key,
                                },
                            }
                        ]
                    },
                    "limit": 1,
                    "with_payload": True,
                    "with_vector": False,
                },
            )

            response.raise_for_status()

        points = (
            response.json()
            .get(
                "result",
                {},
            )
            .get(
                "points",
                [],
            )
        )

        if not points:
            return None

        return points[0].get(
            "payload",
            {},
        )

    def delete_source(
        self,
        collection: str,
        source_key: str,
    ) -> None:
        """Удалить старые points одного источника."""

        with httpx.Client(
            timeout=60.0,
        ) as client:
            response = client.post(
                f"{self.base_url}"
                f"/collections/{collection}"
                "/points/delete",
                params={
                    "wait": "true",
                },
                json={
                    "filter": {
                        "must": [
                            {
                                "key": "source_key",
                                "match": {
                                    "value": source_key,
                                },
                            }
                        ]
                    }
                },
            )

            response.raise_for_status()

    def upsert(
        self,
        collection: str,
        points: list[dict[str, Any]],
    ) -> None:
        """Добавить или обновить points."""

        if not points:
            return

        with httpx.Client(
            timeout=120.0,
        ) as client:
            response = client.put(
                f"{self.base_url}"
                f"/collections/{collection}"
                "/points",
                params={
                    "wait": "true",
                },
                json={
                    "points": points,
                },
            )

            response.raise_for_status()

    def query(
        self,
        collection: str,
        vector: list[float],
        limit: int,
    ) -> list[dict[str, Any]]:
        """Выполнить поиск ближайших векторов."""

        with httpx.Client(
            timeout=60.0,
        ) as client:
            response = client.post(
                f"{self.base_url}"
                f"/collections/{collection}"
                "/points/query",
                json={
                    "query": vector,
                    "limit": limit,
                    "with_payload": True,
                    "with_vector": False,
                },
            )

            response.raise_for_status()

        result = response.json().get(
            "result",
            {},
        )

        return result.get(
            "points",
            [],
        )


def stable_point_id(
    *parts: object,
) -> str:
    """Получить стабильный UUID для Qdrant point."""

    value = "::".join(
        str(part)
        for part in parts
    )

    return str(
        uuid5(
            POINT_NAMESPACE,
            value,
        )
    )