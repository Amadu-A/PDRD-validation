# services/knowledge-service/src/pdrd_knowledge_service/domain/normative_indexing.py

"""Domain-модели и чистые функции нормативной индексации."""

import re
from dataclasses import dataclass
from uuid import (
    NAMESPACE_URL,
    UUID,
    uuid5,
)


class NormativeIndexingPreparationError(RuntimeError):
    """Ошибка подготовки содержимого нормативного документа."""


@dataclass(frozen=True, slots=True)
class NormativeTextPage:
    """Текст одной физической PDF-страницы."""

    page_number: int

    text: str


@dataclass(frozen=True, slots=True)
class NormativeChunk:
    """Один индексируемый нормативный фрагмент."""

    page_number: int

    chunk_index: int

    text: str


def normalize_normative_text(
    text: str,
) -> str:
    """Нормализует текст, сохраняя структуру абзацев."""
    normalized = text.replace(
        "\x00",
        " ",
    )

    normalized = re.sub(
        r"[ \t]+",
        " ",
        normalized,
    )

    normalized = re.sub(
        r"\n{3,}",
        "\n\n",
        normalized,
    )

    return normalized.strip()


def chunk_normative_pages(
    pages: tuple[
        NormativeTextPage,
        ...,
    ],
    *,
    chunk_size: int,
    overlap: int,
) -> tuple[
    NormativeChunk,
    ...,
]:
    """Разбивает каждую PDF-страницу на overlapping chunks."""
    if chunk_size <= 0:
        raise NormativeIndexingPreparationError(
            "Размер нормативного chunk должен быть положительным.",
        )

    if overlap < 0 or overlap >= chunk_size:
        raise NormativeIndexingPreparationError(
            "Normative overlap должен быть >= 0 и меньше chunk_size.",
        )

    result: list[NormativeChunk] = []

    for page in pages:
        normalized = normalize_normative_text(
            page.text,
        )

        if not normalized:
            continue

        start = 0
        chunk_index = 1

        while start < len(
            normalized,
        ):
            end = min(
                start + chunk_size,
                len(
                    normalized,
                ),
            )

            text = normalized[start:end].strip()

            if text:
                result.append(
                    NormativeChunk(
                        page_number=page.page_number,
                        chunk_index=chunk_index,
                        text=text,
                    )
                )

                chunk_index += 1

            if end >= len(
                normalized,
            ):
                break

            start = end - overlap

    return tuple(
        result,
    )


def stable_normative_point_id(
    *,
    document_id: UUID,
    page_number: int,
    chunk_index: int,
) -> str:
    """Возвращает deterministic UUID Qdrant point."""
    identity = f"pdrd:normative:{document_id}:{page_number}:{chunk_index}"

    return str(
        uuid5(
            NAMESPACE_URL,
            identity,
        )
    )
