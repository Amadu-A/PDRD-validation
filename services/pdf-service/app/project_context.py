# services/pdf-service/app/project_context.py

"""Временный RAG-контекст пояснительной записки текущего проекта."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from typing import Any

import httpx


logger = logging.getLogger("uvicorn.error")

OLLAMA_BASE_URL = os.getenv(
    "OLLAMA_BASE_URL",
    "http://ollama:11434",
).rstrip("/")

OLLAMA_VISION_MODEL = os.getenv(
    "OLLAMA_VISION_MODEL",
    "qwen3-vl:8b-instruct",
)

OLLAMA_EMBEDDING_MODEL = os.getenv(
    "OLLAMA_EMBEDDING_MODEL",
    "qwen3-embedding:4b",
)

QDRANT_URL = os.getenv(
    "QDRANT_URL",
    "http://qdrant:6333",
).rstrip("/")

PROJECT_CONTEXT_CHUNK_SIZE = int(
    os.getenv(
        "PROJECT_CONTEXT_CHUNK_SIZE",
        "1800",
    )
)

PROJECT_CONTEXT_CHUNK_OVERLAP = int(
    os.getenv(
        "PROJECT_CONTEXT_CHUNK_OVERLAP",
        "250",
    )
)

PROJECT_CONTEXT_TOP_K = int(
    os.getenv(
        "PROJECT_CONTEXT_TOP_K",
        "5",
    )
)

PROJECT_CONTEXT_TEXT_LIMIT = int(
    os.getenv(
        "PROJECT_CONTEXT_TEXT_LIMIT",
        "900",
    )
)

PROJECT_CONTEXT_CLASSIFY_BATCH_SIZE = int(
    os.getenv(
        "PROJECT_CONTEXT_CLASSIFY_BATCH_SIZE",
        "8",
    )
)

PROJECT_CONTEXT_CLASSIFY_NUM_PREDICT = int(
    os.getenv(
        "PROJECT_CONTEXT_CLASSIFY_NUM_PREDICT",
        "1200",
    )
)

PROJECT_CONTEXT_EMBED_BATCH_SIZE = int(
    os.getenv(
        "PROJECT_CONTEXT_EMBED_BATCH_SIZE",
        "12",
    )
)

PROJECT_CONTEXT_COLLECTION_PREFIX = os.getenv(
    "PROJECT_CONTEXT_COLLECTION_PREFIX",
    "pdrd_project_context",
)

PROJECT_CONTEXT_QUERY_INSTRUCTION = (
    "Given the content of an engineering drawing from the same project, "
    "retrieve the most relevant fragments of the project's explanatory note. "
    "Prefer fragments about the same equipment, tags, cables, functions, "
    "installation conditions, technical solutions and design assumptions."
)


def normalize_text(
    text: str,
) -> str:
    """Нормализовать текст без потери переносов абзацев."""

    text = text.replace(
        "\x00",
        " ",
    )

    text = re.sub(
        r"[ \t]+",
        " ",
        text,
    )

    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text,
    )

    return text.strip()


def chunk_text(
    text: str,
    *,
    chunk_size: int = PROJECT_CONTEXT_CHUNK_SIZE,
    overlap: int = PROJECT_CONTEXT_CHUNK_OVERLAP,
) -> list[str]:
    """Разбить текст страницы ПЗ на перекрывающиеся chunks."""

    normalized = normalize_text(
        text
    )

    if not normalized:
        return []

    if chunk_size <= 0:
        raise ValueError(
            "PROJECT_CONTEXT_CHUNK_SIZE "
            "должен быть положительным."
        )

    if (
        overlap < 0
        or overlap >= chunk_size
    ):
        raise ValueError(
            "PROJECT_CONTEXT_CHUNK_OVERLAP должен быть >= 0 "
            "и меньше PROJECT_CONTEXT_CHUNK_SIZE."
        )

    chunks: list[str] = []

    start = 0

    while start < len(
        normalized
    ):
        end = min(
            start + chunk_size,
            len(
                normalized
            ),
        )

        chunk = normalized[
            start:end
        ].strip()

        if chunk:
            chunks.append(
                chunk
            )

        if end >= len(
            normalized
        ):
            break

        start = (
            end - overlap
        )

    return chunks


async def _post_ollama_embed(
    texts: list[str],
) -> list[list[float]]:
    """Получить document/query embeddings через shared Ollama."""

    if not texts:
        return []

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
                    "model": (
                        OLLAMA_EMBEDDING_MODEL
                    ),
                    "input": texts,
                    "truncate": True,
                },
            )

            response.raise_for_status()

    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            "Ollama вернул ошибку при построении "
            "Project Context embeddings: "
            f"{exc.response.status_code}: "
            f"{exc.response.text[:1000]}"
        ) from exc

    except httpx.HTTPError as exc:
        raise RuntimeError(
            "Не удалось обратиться "
            f"к Ollama embeddings: {exc}"
        ) from exc

    embeddings = response.json().get(
        "embeddings"
    )

    if (
        not isinstance(
            embeddings,
            list,
        )
        or len(
            embeddings
        )
        != len(
            texts
        )
    ):
        raise RuntimeError(
            "Ollama вернул некорректное количество "
            "Project Context embeddings."
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
            "Ollama вернул пустой или некорректный "
            "Project Context embedding."
        )

    return embeddings


async def _embed_documents(
    texts: list[str],
) -> list[list[float]]:
    """Индексировать документы без query instruction."""

    result: list[
        list[float]
    ] = []

    for start in range(
        0,
        len(
            texts
        ),
        PROJECT_CONTEXT_EMBED_BATCH_SIZE,
    ):
        batch = texts[
            start:
            start
            + PROJECT_CONTEXT_EMBED_BATCH_SIZE
        ]

        result.extend(
            await _post_ollama_embed(
                batch
            )
        )

    return result


async def _embed_query(
    query: str,
) -> list[float]:
    """Получить instruction-aware embedding поискового запроса."""

    normalized = normalize_text(
        query
    )

    if not normalized:
        raise ValueError(
            "Запрос к контексту ПЗ "
            "не может быть пустым."
        )

    prepared = (
        f"Instruct: "
        f"{PROJECT_CONTEXT_QUERY_INSTRUCTION}\n"
        f"Query: {normalized}"
    )

    return (
        await _post_ollama_embed(
            [
                prepared
            ]
        )
    )[0]


def _classification_schema(
    page_numbers: list[int],
) -> dict[str, Any]:
    """JSON Schema для проверки выбранных пользователем страниц."""

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "pages": {
                "type": "array",
                "minItems": len(
                    page_numbers
                ),
                "maxItems": len(
                    page_numbers
                ),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "page": {
                            "type": "integer",
                            "enum": (
                                page_numbers
                            ),
                        },
                        "kind": {
                            "type": "string",
                            "enum": [
                                "explanatory_note",
                                "drawing",
                                "specification",
                                "other",
                            ],
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                        },
                        "reason": {
                            "type": "string",
                            "maxLength": 250,
                        },
                    },
                    "required": [
                        "page",
                        "kind",
                        "confidence",
                        "reason",
                    ],
                },
            }
        },
        "required": [
            "pages"
        ],
    }


async def _classify_text_batch(
    pages: list[
        dict[str, Any]
    ],
) -> list[dict[str, Any]]:
    """Проверить, похожи ли страницы на пояснительную записку."""

    page_numbers = [
        int(
            item[
                "page"
            ]
        )
        for item in pages
    ]

    payload = [
        {
            "page": item[
                "page"
            ],
            "text": normalize_text(
                str(
                    item[
                        "text"
                    ]
                )
            )[:2200],
        }
        for item in pages
    ]

    prompt = f"""
Проверь страницы, которые пользователь указал как диапазон
ПОЯСНИТЕЛЬНОЙ ЗАПИСКИ проектной/рабочей документации.

Данные:
{json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}

Для каждой страницы выбери ровно один kind:

- explanatory_note:
  связный текст ПЗ, общие положения, описание проектных решений,
  технические требования, описание оборудования/систем,
  текстовые разделы с допустимыми небольшими таблицами;

- drawing:
  основное содержание страницы — схема, план, чертёж,
  графическое расположение элементов;

- specification:
  основное содержание — спецификация оборудования, изделий,
  материалов, кабельный журнал или большая табличная ведомость;

- other:
  титульный лист, пустая страница или материал,
  который нельзя уверенно считать ПЗ.

Классифицируй только по переданному тексту.
Не ищи ошибки проекта.
Не придумывай отсутствующий текст.
Верни только JSON.
""".strip()

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                600.0,
                connect=20.0,
            ),
        ) as client:
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json={
                    "model": (
                        OLLAMA_VISION_MODEL
                    ),
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                prompt
                            ),
                        }
                    ],
                    "stream": False,
                    "think": False,
                    "format": (
                        _classification_schema(
                            page_numbers
                        )
                    ),
                    "keep_alive": "15m",
                    "options": {
                        "temperature": 0.0,
                        "num_ctx": 8192,
                        "num_predict": (
                            PROJECT_CONTEXT_CLASSIFY_NUM_PREDICT
                        ),
                    },
                },
            )

            response.raise_for_status()

    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            "Ollama вернул ошибку при проверке "
            "диапазона ПЗ: "
            f"{exc.response.status_code}: "
            f"{exc.response.text[:1000]}"
        ) from exc

    except httpx.HTTPError as exc:
        raise RuntimeError(
            "Не удалось проверить страницы ПЗ "
            f"через Ollama: {exc}"
        ) from exc

    content = str(
        response.json()
        .get(
            "message",
            {},
        )
        .get(
            "content",
            "",
        )
    )

    try:
        parsed = json.loads(
            content
        )

    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Модель не смогла классифицировать "
            "диапазон ПЗ корректным JSON: "
            f"{content[:1200]}"
        ) from exc

    items = parsed.get(
        "pages",
        [],
    )

    if not isinstance(
        items,
        list,
    ):
        raise RuntimeError(
            "Некорректный формат результата "
            "проверки ПЗ."
        )

    return items


async def validate_explanatory_note_pages(
    page_texts: dict[
        int,
        str,
    ],
) -> dict[str, Any]:
    """Проверить выбранный диапазон перед индексацией."""

    if not page_texts:
        raise ValueError(
            "Диапазон ПЗ не содержит страниц."
        )

    too_short = [
        page_number
        for (
            page_number,
            text,
        ) in page_texts.items()
        if len(
            normalize_text(
                text
            )
        )
        < 80
    ]

    if too_short:
        raise ValueError(
            "На страницах ПЗ недостаточно "
            "извлекаемого текста: "
            + ", ".join(
                map(
                    str,
                    too_short,
                )
            )
            + ". Проверьте диапазон. "
            "Для сканированной ПЗ позже потребуется OCR."
        )

    ordered = [
        {
            "page": page_number,
            "text": page_texts[
                page_number
            ],
        }
        for page_number in sorted(
            page_texts
        )
    ]

    classifications: list[
        dict[str, Any]
    ] = []

    for start in range(
        0,
        len(
            ordered
        ),
        PROJECT_CONTEXT_CLASSIFY_BATCH_SIZE,
    ):
        batch = ordered[
            start:
            start
            + PROJECT_CONTEXT_CLASSIFY_BATCH_SIZE
        ]

        classifications.extend(
            await _classify_text_batch(
                batch
            )
        )

    by_page = {
        int(
            item[
                "page"
            ]
        ): item
        for item in classifications
        if (
            isinstance(
                item,
                dict,
            )
            and "page" in item
        )
    }

    missing_pages = [
        page_number
        for page_number in sorted(
            page_texts
        )
        if page_number not in by_page
    ]

    if missing_pages:
        raise RuntimeError(
            "Модель не вернула классификацию "
            "страниц ПЗ: "
            + ", ".join(
                map(
                    str,
                    missing_pages,
                )
            )
        )

    rejected = []
    warnings = []

    for page_number in sorted(
        page_texts
    ):
        item = by_page[
            page_number
        ]

        kind = str(
            item.get(
                "kind",
                "other",
            )
        )

        confidence = float(
            item.get(
                "confidence",
                0.0,
            )
        )

        if (
            kind
            != "explanatory_note"
            and confidence >= 0.75
        ):
            rejected.append(
                item
            )

        elif (
            kind
            != "explanatory_note"
        ):
            warnings.append(
                item
            )

    if rejected:
        details = "; ".join(
            f"стр. {item['page']}: "
            f"{item.get('kind')} "
            f"({item.get('reason', '')})"
            for item in rejected
        )

        raise ValueError(
            "Выбранный диапазон похож не только "
            "на пояснительную записку. "
            f"Проверьте страницы: {details}"
        )

    return {
        "status": "accepted",
        "pages_count": len(
            page_texts
        ),
        "classifications": [
            by_page[
                page_number
            ]
            for page_number in sorted(
                page_texts
            )
        ],
        "warnings": warnings,
    }


async def create_temporary_project_context(
    page_texts: dict[
        int,
        str,
    ],
) -> dict[str, Any]:
    """Создать уникальную временную Qdrant collection для одной проверки."""

    records: list[
        dict[str, Any]
    ] = []

    for page_number in sorted(
        page_texts
    ):
        chunks = chunk_text(
            page_texts[
                page_number
            ]
        )

        for (
            chunk_index,
            chunk,
        ) in enumerate(
            chunks,
            start=1,
        ):
            records.append(
                {
                    "page": (
                        page_number
                    ),
                    "chunk_index": (
                        chunk_index
                    ),
                    "text": (
                        chunk
                    ),
                }
            )

    if not records:
        raise ValueError(
            "В диапазоне ПЗ нет текста "
            "для индексации."
        )

    vectors = await _embed_documents(
        [
            record[
                "text"
            ]
            for record in records
        ]
    )

    vector_size = len(
        vectors[0]
    )

    collection_name = (
        f"{PROJECT_CONTEXT_COLLECTION_PREFIX}_"
        f"{uuid.uuid4().hex}"
    )

    try:
        async with httpx.AsyncClient(
            timeout=90.0,
        ) as client:
            response = await client.put(
                f"{QDRANT_URL}/collections/"
                f"{collection_name}",
                json={
                    "vectors": {
                        "size": (
                            vector_size
                        ),
                        "distance": (
                            "Cosine"
                        ),
                    }
                },
            )

            response.raise_for_status()

            points = [
                {
                    "id": str(
                        uuid.uuid4()
                    ),
                    "vector": vector,
                    "payload": (
                        record
                    ),
                }
                for (
                    record,
                    vector,
                ) in zip(
                    records,
                    vectors,
                    strict=True,
                )
            ]

            for start in range(
                0,
                len(
                    points
                ),
                64,
            ):
                batch = points[
                    start:
                    start + 64
                ]

                response = await client.put(
                    f"{QDRANT_URL}/collections/"
                    f"{collection_name}/points",
                    params={
                        "wait": "true"
                    },
                    json={
                        "points": batch
                    },
                )

                response.raise_for_status()

    except httpx.HTTPStatusError as exc:
        await delete_temporary_project_context(
            collection_name
        )

        raise RuntimeError(
            "Qdrant вернул ошибку при создании "
            "временного контекста ПЗ: "
            f"{exc.response.status_code}: "
            f"{exc.response.text[:1000]}"
        ) from exc

    except httpx.HTTPError as exc:
        await delete_temporary_project_context(
            collection_name
        )

        raise RuntimeError(
            "Не удалось создать временный "
            f"контекст ПЗ в Qdrant: {exc}"
        ) from exc

    logger.info(
        "[project_context] CREATED "
        "collection=%s pages=%s chunks=%s",
        collection_name,
        len(
            page_texts
        ),
        len(
            records
        ),
    )

    return {
        "collection_name": (
            collection_name
        ),
        "pages_count": len(
            page_texts
        ),
        "chunks_count": len(
            records
        ),
        "vector_size": (
            vector_size
        ),
    }


async def search_project_context(
    collection_name: str,
    query: str,
) -> list[dict[str, Any]]:
    """Найти релевантные фрагменты ПЗ для текущего листа."""

    vector = await _embed_query(
        query
    )

    try:
        async with httpx.AsyncClient(
            timeout=90.0,
        ) as client:
            response = await client.post(
                f"{QDRANT_URL}/collections/"
                f"{collection_name}/points/query",
                json={
                    "query": vector,
                    "limit": (
                        PROJECT_CONTEXT_TOP_K
                    ),
                    "with_payload": True,
                    "with_vector": False,
                },
            )

            response.raise_for_status()

    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            "Qdrant вернул ошибку при поиске "
            "по контексту ПЗ: "
            f"{exc.response.status_code}: "
            f"{exc.response.text[:1000]}"
        ) from exc

    except httpx.HTTPError as exc:
        raise RuntimeError(
            "Не удалось выполнить поиск "
            f"по контексту ПЗ: {exc}"
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
            "Project Context result."
        )

    result = []

    for (
        index,
        point,
    ) in enumerate(
        points,
        start=1,
    ):
        payload = point.get(
            "payload",
            {},
        )

        result.append(
            {
                "source_id": (
                    f"PZ{index}"
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
                "page": payload.get(
                    "page"
                ),
                "chunk_index": (
                    payload.get(
                        "chunk_index"
                    )
                ),
                "text": str(
                    payload.get(
                        "text",
                        "",
                    )
                ),
            }
        )

    return result


async def delete_temporary_project_context(
    collection_name: str | None,
) -> None:
    """Удалить временную collection даже после ошибки анализа."""

    if not collection_name:
        return

    try:
        async with httpx.AsyncClient(
            timeout=30.0,
        ) as client:
            response = await client.delete(
                f"{QDRANT_URL}/collections/"
                f"{collection_name}"
            )

            if response.status_code not in {
                200,
                404,
            }:
                logger.warning(
                    "[project_context] DELETE failed "
                    "collection=%s status=%s body=%s",
                    collection_name,
                    response.status_code,
                    response.text[:500],
                )

                return

    except httpx.HTTPError as exc:
        logger.warning(
            "[project_context] DELETE failed "
            "collection=%s error=%s",
            collection_name,
            exc,
        )

        return

    logger.info(
        "[project_context] DELETED "
        "collection=%s",
        collection_name,
    )


def build_project_context_query(
    *,
    page_facts: dict[str, Any],
    extracted_text: str,
) -> str:
    """Сформировать запрос ПЗ по фактам анализируемого листа."""

    objects = "; ".join(
        str(
            item
        )
        for item in (
            page_facts.get(
                "objects",
                [],
            )[:10]
        )
    )

    connections = "; ".join(
        str(
            item
        )
        for item in (
            page_facts.get(
                "connections",
                [],
            )[:8]
        )
    )

    labels = "; ".join(
        str(
            item
        )
        for item in (
            page_facts.get(
                "labels",
                [],
            )[:10]
        )
    )

    return (
        "Найти в пояснительной записке текущего проекта "
        "сведения, которые относятся к этому листу. "
        f"Дисциплина: "
        f"{page_facts.get('discipline', '')}. "
        f"Тип листа: "
        f"{page_facts.get('page_type', '')}. "
        f"Содержание: "
        f"{page_facts.get('summary', '')}. "
        f"Объекты: {objects}. "
        f"Связи: {connections}. "
        f"Обозначения: {labels}. "
        f"Текст листа: "
        f"{normalize_text(extracted_text)[:1500]}"
    )


def build_augmented_page_text(
    *,
    extracted_text: str,
    project_context_sources: list[
        dict[str, Any]
    ],
) -> str:
    """Добавить найденный контекст ПЗ в нормативную проверку."""

    if not project_context_sources:
        return extracted_text

    context_parts = []

    for source in (
        project_context_sources
    ):
        context_parts.append(
            "["
            f"{source.get('source_id')} | "
            f"PDF стр. {source.get('page')} | "
            f"similarity={source.get('score')}"
            "]\n"
            f"{str(source.get('text', ''))[:PROJECT_CONTEXT_TEXT_LIMIT]}"
        )

    return (
        "=== ТЕКСТ АНАЛИЗИРУЕМОЙ СТРАНИЦЫ ===\n"
        f"{extracted_text}\n\n"
        "=== РЕЛЕВАНТНЫЙ КОНТЕКСТ "
        "ПОЯСНИТЕЛЬНОЙ ЗАПИСКИ ===\n"
        + "\n\n".join(
            context_parts
        )
        + "\n\n"
        "Контекст ПЗ является контекстом текущего проекта, "
        "а не нормативом. Используй его для понимания "
        "проектных решений и применимости норм. "
        "Нормативное нарушение по-прежнему должно "
        "подтверждаться реальным нормативным источником N-id."
    )


def compact_project_context_for_api(
    sources: list[
        dict[str, Any]
    ],
) -> list[dict[str, Any]]:
    """Сократить PZ chunks для API/frontend."""

    return [
        {
            "source_id": source.get(
                "source_id"
            ),
            "score": source.get(
                "score"
            ),
            "page": source.get(
                "page"
            ),
            "chunk_index": source.get(
                "chunk_index"
            ),
            "text_excerpt": str(
                source.get(
                    "text",
                    "",
                )
            )[:500],
        }
        for source in sources
    ]
