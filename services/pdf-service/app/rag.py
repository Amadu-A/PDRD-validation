# services/pdf-service/app/rag.py

"""Поиск по нормативной и опытной базе знаний."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx


OLLAMA_BASE_URL = os.getenv(
    "OLLAMA_BASE_URL",
    "http://ollama:11434",
).rstrip("/")

OLLAMA_EMBEDDING_MODEL = os.getenv(
    "OLLAMA_EMBEDDING_MODEL",
    "qwen3-embedding:0.6b",
)

QDRANT_URL = os.getenv(
    "QDRANT_URL",
    "http://qdrant:6333",
).rstrip("/")

QDRANT_NORMATIVE_COLLECTION = os.getenv(
    "QDRANT_NORMATIVE_COLLECTION",
    "dva_normative",
)

QDRANT_EXPERIENCE_COLLECTION = os.getenv(
    "QDRANT_EXPERIENCE_COLLECTION",
    "dva_experience",
)

RAG_NORMATIVE_TOP_K = int(
    os.getenv(
        "RAG_NORMATIVE_TOP_K",
        "5",
    )
)

RAG_EXPERIENCE_TOP_K = int(
    os.getenv(
        "RAG_EXPERIENCE_TOP_K",
        "3",
    )
)


async def embed_text(
    text: str,
) -> list[float]:
    """Получить embedding текста через Ollama."""

    if not text.strip():
        raise ValueError(
            "Нельзя построить embedding пустого текста."
        )

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                600.0,
                connect=20.0,
            ),
        ) as client:
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/embed",
                json={
                    "model": OLLAMA_EMBEDDING_MODEL,
                    "input": text,
                    "truncate": True,
                },
            )

            response.raise_for_status()

    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            "Ollama вернул ошибку при "
            "построении embedding: "
            f"{exc.response.status_code}: "
            f"{exc.response.text[:1000]}"
        ) from exc

    except httpx.HTTPError as exc:
        raise RuntimeError(
            "Не удалось обратиться "
            f"к Ollama embeddings: {exc}"
        ) from exc

    embeddings = response.json().get(
        "embeddings",
    )

    if (
        not isinstance(
            embeddings,
            list,
        )
        or not embeddings
        or not isinstance(
            embeddings[0],
            list,
        )
    ):
        raise RuntimeError(
            "Ollama вернул некорректный embedding."
        )

    return embeddings[0]


async def query_collection(
    *,
    collection: str,
    vector: list[float],
    limit: int,
) -> list[dict[str, Any]]:
    """Найти ближайшие записи в одной коллекции."""

    try:
        async with httpx.AsyncClient(
            timeout=60.0,
        ) as client:
            response = await client.post(
                f"{QDRANT_URL}"
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

    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"Qdrant collection {collection} "
            "вернула ошибку: "
            f"{exc.response.status_code}: "
            f"{exc.response.text[:1000]}"
        ) from exc

    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"Не удалось обратиться к "
            f"Qdrant collection {collection}: "
            f"{exc}"
        ) from exc

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

    if not isinstance(
        points,
        list,
    ):
        raise RuntimeError(
            "Qdrant вернул некорректный "
            "формат points."
        )

    return points


def normalize_normative_results(
    points: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Привести нормативные результаты к компактному формату."""

    result: list[
        dict[str, Any]
    ] = []

    for index, point in enumerate(
        points,
        start=1,
    ):
        payload = point.get(
            "payload",
            {},
        )

        result.append(
            {
                "source_id": f"N{index}",
                "score": round(
                    float(
                        point.get(
                            "score",
                            0.0,
                        )
                    ),
                    4,
                ),
                "source_file": payload.get(
                    "source_file",
                ),
                "source_path": payload.get(
                    "source_path",
                ),
                "page": payload.get(
                    "page",
                ),
                "chunk_index": payload.get(
                    "chunk_index",
                ),
                "text": payload.get(
                    "text",
                    "",
                ),
            }
        )

    return result


def normalize_experience_results(
    points: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Привести экспертные примеры к компактному формату."""

    result: list[
        dict[str, Any]
    ] = []

    for index, point in enumerate(
        points,
        start=1,
    ):
        payload = point.get(
            "payload",
            {},
        )

        result.append(
            {
                "source_id": f"E{index}",
                "score": round(
                    float(
                        point.get(
                            "score",
                            0.0,
                        )
                    ),
                    4,
                ),
                "project_id": payload.get(
                    "project_id",
                ),
                "issue_id": payload.get(
                    "issue_id",
                ),
                "issue_text": payload.get(
                    "issue_text",
                ),
                "before_page": payload.get(
                    "before_page",
                ),
                "after_page": payload.get(
                    "after_page",
                ),
            }
        )

    return result


async def search_knowledge(
    query: str,
) -> dict[str, Any]:
    """Искать одновременно по нормативам и Базе Опыта."""

    normalized_query = query.strip()

    if not normalized_query:
        raise ValueError(
            "Поисковый запрос не может быть пустым."
        )

    vector = await embed_text(
        normalized_query
    )

    (
        normative_points,
        experience_points,
    ) = await asyncio.gather(
        query_collection(
            collection=(
                QDRANT_NORMATIVE_COLLECTION
            ),
            vector=vector,
            limit=RAG_NORMATIVE_TOP_K,
        ),
        query_collection(
            collection=(
                QDRANT_EXPERIENCE_COLLECTION
            ),
            vector=vector,
            limit=RAG_EXPERIENCE_TOP_K,
        ),
    )

    return {
        "status": "ok",
        "query": normalized_query,
        "embedding_model": (
            OLLAMA_EMBEDDING_MODEL
        ),
        "vector_size": len(
            vector
        ),
        "collections": {
            "normative": (
                QDRANT_NORMATIVE_COLLECTION
            ),
            "experience": (
                QDRANT_EXPERIENCE_COLLECTION
            ),
        },
        "normative": (
            normalize_normative_results(
                normative_points
            )
        ),
        "experience": (
            normalize_experience_results(
                experience_points
            )
        ),
    }
