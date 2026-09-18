# services/knowledge-service/src/pdrd_knowledge_service/domain/project_context.py

"""Domain-модели Project Context / Пояснительной записки."""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import (
    NAMESPACE_URL,
    UUID,
    uuid5,
)


class ProjectContextError(RuntimeError):
    """Ошибка подготовки или поиска Project Context."""


@dataclass(frozen=True, slots=True)
class ProjectContextTextPage:
    """Text-only страница Пояснительной записки."""

    page_number: int

    text: str


@dataclass(frozen=True, slots=True)
class ProjectContextChunk:
    """Один индексируемый фрагмент ПЗ."""

    page_number: int

    chunk_index: int

    text: str


@dataclass(frozen=True, slots=True)
class ProjectContextValidationItem:
    """Сохранённая классификация одной страницы ПЗ."""

    page_number: int

    kind: str

    confidence: float

    reason: str


@dataclass(frozen=True, slots=True)
class ProjectContextValidationSnapshot:
    """Сохранённый результат VLM-проверки диапазона ПЗ."""

    enabled: bool

    pages_count: int

    classifications: tuple[
        ProjectContextValidationItem,
        ...,
    ]

    warnings: tuple[
        ProjectContextValidationItem,
        ...,
    ]


@dataclass(frozen=True, slots=True)
class VectorRecord:
    """Vector record без зависимости от Qdrant."""

    point_id: str

    vector: list[float]

    payload: dict[
        str,
        Any,
    ]


@dataclass(frozen=True, slots=True)
class ProjectContextCacheStatus:
    """Результат поиска reusable PZ cache."""

    context_id: UUID

    enabled: bool

    cache_key: str | None

    cache_hit: bool

    collection_name: str | None

    pages_count: int

    chunks_count: int

    vector_size: int

    validation: ProjectContextValidationSnapshot | None = None


@dataclass(frozen=True, slots=True)
class ProjectContextInfo:
    """Информация о prepared Project Context."""

    context_id: UUID

    enabled: bool

    collection_name: str | None

    pages_count: int

    chunks_count: int

    vector_size: int

    cache_key: str | None = None

    cache_hit: bool = False

    validation: ProjectContextValidationSnapshot | None = None


@dataclass(frozen=True, slots=True)
class ProjectContextSource:
    """Semantic source из текущей ПЗ."""

    source_id: str

    point_id: str

    score: float

    page: int | None

    chunk_index: int | None

    text: str


@dataclass(frozen=True, slots=True)
class ProjectContextSearchResult:
    """Результат semantic retrieval по ПЗ."""

    context_id: UUID

    query: str

    sources: tuple[
        ProjectContextSource,
        ...,
    ]

    embedding_model: str


def normalize_project_context_text(
    text: str,
) -> str:
    """Нормализует текст без потери paragraph structure."""
    result = text.replace(
        "\x00",
        " ",
    )

    result = re.sub(
        r"[ \t]+",
        " ",
        result,
    )

    result = re.sub(
        r"\n{3,}",
        "\n\n",
        result,
    )

    return result.strip()


def normalize_project_context_pages(
    pages: tuple[
        ProjectContextTextPage,
        ...,
    ],
) -> tuple[
    ProjectContextTextPage,
    ...,
]:
    """Нормализует и детерминированно сортирует страницы ПЗ."""
    normalized = tuple(
        ProjectContextTextPage(
            page_number=page.page_number,
            text=normalize_project_context_text(
                page.text,
            ),
        )
        for page in sorted(
            pages,
            key=lambda item: item.page_number,
        )
    )

    page_numbers = tuple(page.page_number for page in normalized)

    if any(page_number < 1 for page_number in page_numbers):
        raise ProjectContextError(
            "Номер страницы Project Context должен быть положительным.",
        )

    if len(
        set(
            page_numbers,
        )
    ) != len(
        page_numbers,
    ):
        raise ProjectContextError(
            "Диапазон Project Context содержит повторяющиеся номера страниц.",
        )

    return normalized


def project_context_cache_key(
    *,
    pages: tuple[
        ProjectContextTextPage,
        ...,
    ],
    embedding_model: str,
    embedding_dimension: int,
    embedding_schema_version: int,
    chunk_size: int,
    chunk_overlap: int,
    cache_schema_version: int,
) -> str:
    """Строит content-addressed identity reusable PZ cache."""
    normalized_pages = normalize_project_context_pages(
        pages,
    )

    canonical_payload = {
        "cache_schema_version": cache_schema_version,
        "embedding": {
            "model": embedding_model,
            "dimension": embedding_dimension,
            "schema_version": embedding_schema_version,
        },
        "chunking": {
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
        },
        "pages": [
            {
                "page_number": page.page_number,
                "text": page.text,
            }
            for page in normalized_pages
        ],
    }

    encoded = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(
            ",",
            ":",
        ),
    ).encode(
        "utf-8",
    )

    return hashlib.sha256(
        encoded,
    ).hexdigest()


def project_context_cache_context_id(
    cache_key: str,
) -> UUID:
    """Преобразует cache SHA-256 в стабильный UUID."""
    normalized = cache_key.strip().lower()

    if not normalized:
        raise ProjectContextError(
            "Project Context cache key пуст.",
        )

    return uuid5(
        NAMESPACE_URL,
        f"pdrd-project-context-cache:{normalized}",
    )


def chunk_project_context_text(
    text: str,
    *,
    chunk_size: int,
    overlap: int,
) -> tuple[str, ...]:
    """Разбивает страницу ПЗ на overlapping chunks."""
    normalized = normalize_project_context_text(
        text,
    )

    if not normalized:
        return ()

    if chunk_size <= 0:
        raise ProjectContextError(
            "Размер Project Context chunk должен быть положительным.",
        )

    if overlap < 0 or overlap >= chunk_size:
        raise ProjectContextError(
            "Project Context overlap должен быть >= 0 и меньше chunk_size.",
        )

    result: list[str] = []

    start = 0

    while start < len(
        normalized,
    ):
        end = min(
            start + chunk_size,
            len(
                normalized,
            ),
        )

        chunk = normalized[start:end].strip()

        if chunk:
            result.append(
                chunk,
            )

        if end >= len(
            normalized,
        ):
            break

        start = end - overlap

    return tuple(
        result,
    )


def project_context_collection_name(
    *,
    prefix: str,
    context_id: UUID,
) -> str:
    """Возвращает stable alias Project Context cache."""
    return f"{prefix}_{context_id.hex}"


def project_context_staging_collection_name(
    *,
    prefix: str,
    context_id: UUID,
    build_id: UUID,
) -> str:
    """Возвращает physical collection незавершённой cache build."""
    return f"{prefix}_{context_id.hex}_build_{build_id.hex}"
