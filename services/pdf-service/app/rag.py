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


async def embed_texts(
    texts: list[str],
) -> list[list[float]]:
    """Получить embeddings нескольких текстов одним запросом к Ollama."""

    normalized = [
        text.strip()
        for text in texts
    ]

    if not normalized:
        return []

    if any(
        not text
        for text in normalized
    ):
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
                    "input": normalized,
                    "truncate": True,
                },
            )

            response.raise_for_status()

    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            "Ollama вернул ошибку при построении embedding: "
            f"{exc.response.status_code}: "
            f"{exc.response.text[:1000]}"
        ) from exc

    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"Не удалось обратиться к Ollama embeddings: {exc}"
        ) from exc

    embeddings = response.json().get(
        "embeddings",
    )

    if not isinstance(
        embeddings,
        list,
    ):
        raise RuntimeError(
            "Ollama вернул некорректный список embeddings."
        )

    if len(embeddings) != len(
        normalized
    ):
        raise RuntimeError(
            "Количество embeddings не совпадает "
            "с количеством поисковых запросов."
        )

    if any(
        not isinstance(
            item,
            list,
        )
        or not item
        for item in embeddings
    ):
        raise RuntimeError(
            "Ollama вернул пустой или некорректный embedding."
        )

    return embeddings


async def embed_text(
    text: str,
) -> list[float]:
    """Получить embedding одного текста."""

    return (
        await embed_texts(
            [text]
        )
    )[0]


async def query_collection(
    *,
    collection: str,
    vector: list[float],
    limit: int,
) -> list[dict[str, Any]]:
    """Найти ближайшие записи в одной коллекции Qdrant."""

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
            f"Qdrant collection {collection}: {exc}"
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
            "Qdrant вернул некорректный формат points."
        )

    return points


def normalize_normative_results(
    points: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Привести результаты нормативной коллекции к API-формату."""

    result = []

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
    """Привести результаты опытной коллекции к API-формату."""

    result = []

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


async def search_knowledge_by_vector(
    *,
    query: str,
    vector: list[float],
) -> dict[str, Any]:
    """Искать по нормативам и опыту для уже рассчитанного вектора."""

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
        "query": query,
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


async def search_knowledge(
    query: str,
) -> dict[str, Any]:
    """Искать один запрос по нормативам и опыту."""

    normalized_query = query.strip()

    if not normalized_query:
        raise ValueError(
            "Поисковый запрос не может быть пустым."
        )

    vector = await embed_text(
        normalized_query
    )

    return await search_knowledge_by_vector(
        query=normalized_query,
        vector=vector,
    )


async def search_knowledge_many(
    queries: list[str],
) -> list[dict[str, Any]]:
    """Искать несколько запросов с одним batch-вызовом embeddings."""

    normalized_queries = [
        query.strip()
        for query in queries
    ]

    if not normalized_queries:
        return []

    if any(
        not query
        for query in normalized_queries
    ):
        raise ValueError(
            "Поисковый запрос не может быть пустым."
        )

    vectors = await embed_texts(
        normalized_queries
    )

    return list(
        await asyncio.gather(
            *[
                search_knowledge_by_vector(
                    query=query,
                    vector=vector,
                )
                for query, vector in zip(
                    normalized_queries,
                    vectors,
                    strict=True,
                )
            ]
        )
    )
