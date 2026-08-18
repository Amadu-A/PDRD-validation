# services/pdf-service/app/rag.py

"""RAG-поиск по нормативной базе и Базе Опыта."""

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
    "qwen3-embedding:4b",
)

QDRANT_URL = os.getenv(
    "QDRANT_URL",
    "http://qdrant:6333",
).rstrip("/")

QDRANT_NORMATIVE_COLLECTION = os.getenv(
    "QDRANT_NORMATIVE_COLLECTION",
    "dva_normative_v2",
)

QDRANT_EXPERIENCE_COLLECTION = os.getenv(
    "QDRANT_EXPERIENCE_COLLECTION",
    "dva_experience_v2",
)

RAG_NORMATIVE_TOP_K = int(
    os.getenv(
        "RAG_NORMATIVE_TOP_K",
        "4",
    )
)

RAG_NORMATIVE_MAX_SOURCES = int(
    os.getenv(
        "RAG_NORMATIVE_MAX_SOURCES",
        "12",
    )
)

RAG_EXPERIENCE_TOP_K = int(
    os.getenv(
        "RAG_EXPERIENCE_TOP_K",
        "3",
    )
)

NORMATIVE_QUERY_INSTRUCTION = (
    "Given a description of a Russian engineering drawing or a technical "
    "check topic, retrieve the most directly applicable normative requirement "
    "for compliance verification."
)

EXPERIENCE_QUERY_INSTRUCTION = (
    "Given an engineering design violation, retrieve similar expert review "
    "comments and correction examples."
)


def _prepare_query(
    query: str,
    instruction: str,
) -> str:
    """Добавить task instruction к поисковому запросу Qwen3-Embedding."""

    normalized = query.strip()

    if not normalized:
        raise ValueError(
            "Поисковый запрос не может быть пустым."
        )

    return (
        f"Instruct: {instruction}\n"
        f"Query: {normalized}"
    )


async def embed_queries(
    queries: list[str],
    *,
    instruction: str,
) -> list[list[float]]:
    """Получить embeddings поисковых запросов одним вызовом Ollama."""

    if not queries:
        return []

    prepared = [
        _prepare_query(
            query,
            instruction,
        )
        for query in queries
    ]

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                900.0,
                connect=20.0,
            ),
        ) as client:
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/embed",
                json={
                    "model": OLLAMA_EMBEDDING_MODEL,
                    "input": prepared,
                    "truncate": True,
                },
            )

            response.raise_for_status()

    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            "Ollama вернул ошибку при построении embeddings: "
            f"{exc.response.status_code}: "
            f"{exc.response.text[:1000]}"
        ) from exc

    except httpx.HTTPError as exc:
        raise RuntimeError(
            "Не удалось обратиться к Ollama embeddings: "
            f"{exc}"
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
        prepared
    ):
        raise RuntimeError(
            "Количество embeddings не совпадает "
            "с количеством запросов."
        )

    if any(
        not isinstance(
            vector,
            list,
        )
        or not vector
        for vector in embeddings
    ):
        raise RuntimeError(
            "Ollama вернул пустой или некорректный embedding."
        )

    return embeddings


async def query_collection(
    *,
    collection: str,
    vector: list[float],
    limit: int,
) -> list[dict[str, Any]]:
    """Выполнить vector search в одной коллекции Qdrant."""

    try:
        async with httpx.AsyncClient(
            timeout=90.0,
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
            f"Qdrant collection {collection} вернула ошибку: "
            f"{exc.response.status_code}: "
            f"{exc.response.text[:1000]}"
        ) from exc

    except httpx.HTTPError as exc:
        raise RuntimeError(
            "Не удалось обратиться к Qdrant "
            f"collection {collection}: {exc}"
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


def _merge_points(
    groups: list[list[dict[str, Any]]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Объединить результаты нескольких запросов без дублей."""

    by_id: dict[
        str,
        dict[str, Any],
    ] = {}

    for points in groups:
        for point in points:
            point_id = str(
                point.get(
                    "id",
                    "",
                )
            )

            if not point_id:
                continue

            previous = by_id.get(
                point_id
            )

            if (
                previous is None
                or float(
                    point.get(
                        "score",
                        0.0,
                    )
                )
                > float(
                    previous.get(
                        "score",
                        0.0,
                    )
                )
            ):
                by_id[
                    point_id
                ] = point

    return sorted(
        by_id.values(),
        key=lambda item: float(
            item.get(
                "score",
                0.0,
            )
        ),
        reverse=True,
    )[:limit]


def normalize_normative_results(
    points: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Привести нормативные результаты к стабильному API-формату."""

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
                "point_id": str(
                    point.get(
                        "id",
                        "",
                    )
                ),
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


def _split_experience_context(
    text: str,
) -> tuple[str, str]:
    """Извлечь BEFORE/AFTER контекст из старого payload, если он есть."""

    before_context = ""
    after_context = ""

    before_marker = (
        "Контекст листа до исправления:"
    )

    after_page_marker = (
        "\n\nСтраница после исправления:"
    )

    after_marker = (
        "Контекст исправленного листа:"
    )

    if before_marker in text:
        tail = text.split(
            before_marker,
            maxsplit=1,
        )[1]

        if after_page_marker in tail:
            before_context, after_tail = (
                tail.split(
                    after_page_marker,
                    maxsplit=1,
                )
            )

            if after_marker in after_tail:
                after_context = (
                    after_tail.split(
                        after_marker,
                        maxsplit=1,
                    )[1]
                )

        else:
            before_context = tail

    return (
        before_context.strip(),
        after_context.strip(),
    )


def normalize_experience_results(
    points: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Привести результаты Базы Опыта к стабильному API-формату."""

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

        raw_text = str(
            payload.get(
                "text",
                "",
            )
        )

        (
            legacy_before,
            legacy_after,
        ) = _split_experience_context(
            raw_text
        )

        result.append(
            {
                "source_id": f"E{index}",
                "point_id": str(
                    point.get(
                        "id",
                        "",
                    )
                ),
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
                "status": payload.get(
                    "status",
                ),
                "verified_fixed": bool(
                    payload.get(
                        "verified_fixed",
                        False,
                    )
                ),
                "before_page": payload.get(
                    "before_page",
                ),
                "after_page": payload.get(
                    "after_page",
                ),
                "before_context": (
                    payload.get(
                        "before_context"
                    )
                    or legacy_before
                ),
                "after_context": (
                    payload.get(
                        "after_context"
                    )
                    or legacy_after
                ),
            }
        )

    return result


async def search_normative(
    queries: list[str],
) -> dict[str, Any]:
    """Найти нормы для набора нейтральных тем проверки листа."""

    normalized_queries: list[
        str
    ] = []

    seen: set[
        str
    ] = set()

    for query in queries:
        normalized = query.strip()

        if (
            not normalized
            or normalized in seen
        ):
            continue

        seen.add(
            normalized
        )

        normalized_queries.append(
            normalized
        )

    if not normalized_queries:
        return {
            "queries": [],
            "sources": [],
            "embedding_model": (
                OLLAMA_EMBEDDING_MODEL
            ),
        }

    vectors = await embed_queries(
        normalized_queries,
        instruction=(
            NORMATIVE_QUERY_INSTRUCTION
        ),
    )

    groups = await asyncio.gather(
        *[
            query_collection(
                collection=(
                    QDRANT_NORMATIVE_COLLECTION
                ),
                vector=vector,
                limit=(
                    RAG_NORMATIVE_TOP_K
                ),
            )
            for vector in vectors
        ]
    )

    merged = _merge_points(
        list(
            groups
        ),
        limit=(
            RAG_NORMATIVE_MAX_SOURCES
        ),
    )

    return {
        "queries": normalized_queries,
        "sources": (
            normalize_normative_results(
                merged
            )
        ),
        "embedding_model": (
            OLLAMA_EMBEDDING_MODEL
        ),
    }


async def search_experience_many(
    queries: list[str],
) -> list[dict[str, Any]]:
    """Найти похожий опыт отдельно для каждого установленного нарушения."""

    normalized = [
        query.strip()
        for query in queries
    ]

    if not normalized:
        return []

    if any(
        not query
        for query in normalized
    ):
        raise ValueError(
            "Запрос к Базе Опыта "
            "не может быть пустым."
        )

    vectors = await embed_queries(
        normalized,
        instruction=(
            EXPERIENCE_QUERY_INSTRUCTION
        ),
    )

    groups = await asyncio.gather(
        *[
            query_collection(
                collection=(
                    QDRANT_EXPERIENCE_COLLECTION
                ),
                vector=vector,
                limit=(
                    RAG_EXPERIENCE_TOP_K
                ),
            )
            for vector in vectors
        ]
    )

    return [
        {
            "query": query,
            "sources": (
                normalize_experience_results(
                    points
                )
            ),
            "embedding_model": (
                OLLAMA_EMBEDDING_MODEL
            ),
        }
        for query, points in zip(
            normalized,
            groups,
            strict=True,
        )
    ]


async def search_knowledge(
    query: str,
) -> dict[str, Any]:
    """Диагностический поиск одного запроса сразу по двум базам."""

    normative = await search_normative(
        [query]
    )

    experience = (
        await search_experience_many(
            [query]
        )
    )

    return {
        "status": "ok",
        "query": query,
        "embedding_model": (
            OLLAMA_EMBEDDING_MODEL
        ),
        "collections": {
            "normative": (
                QDRANT_NORMATIVE_COLLECTION
            ),
            "experience": (
                QDRANT_EXPERIENCE_COLLECTION
            ),
        },
        "normative": normative[
            "sources"
        ],
        "experience": (
            experience[0][
                "sources"
            ]
            if experience
            else []
        ),
    }


async def get_rag_status() -> dict[str, Any]:
    """Проверить доступность Qdrant и обеих необходимых коллекций."""

    try:
        async with httpx.AsyncClient(
            timeout=10.0,
        ) as client:
            response = await client.get(
                f"{QDRANT_URL}/collections"
            )

            response.raise_for_status()

    except httpx.HTTPError as exc:
        return {
            "qdrant": False,
            "collections_ready": False,
            "error": str(
                exc
            ),
        }

    collections = (
        response.json()
        .get(
            "result",
            {},
        )
        .get(
            "collections",
            [],
        )
    )

    names = {
        str(
            item.get(
                "name",
                "",
            )
        )
        for item in collections
    }

    required = {
        QDRANT_NORMATIVE_COLLECTION,
        QDRANT_EXPERIENCE_COLLECTION,
    }

    return {
        "qdrant": True,
        "collections_ready": (
            required.issubset(
                names
            )
        ),
        "required_collections": sorted(
            required
        ),
        "available_collections": sorted(
            name
            for name in names
            if name
        ),
    }
