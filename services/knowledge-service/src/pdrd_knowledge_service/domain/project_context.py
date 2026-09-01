# services/knowledge-service/src/pdrd_knowledge_service/domain/project_context.py

"""Domain-модели временного Project Context index."""

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID


class ProjectContextError(RuntimeError):
    """Ошибка подготовки или поиска временного Project Context."""


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
class VectorRecord:
    """Vector record без зависимости от Qdrant."""

    point_id: str

    vector: list[float]

    payload: dict[
        str,
        Any,
    ]


@dataclass(frozen=True, slots=True)
class ProjectContextInfo:
    """Информация о временном Project Context."""

    context_id: UUID

    enabled: bool

    collection_name: str | None

    pages_count: int

    chunks_count: int

    vector_size: int


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
    """Возвращает deterministic collection name."""
    return f"{prefix}_{context_id.hex}"
