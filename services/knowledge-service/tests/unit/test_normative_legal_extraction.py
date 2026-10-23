# services/knowledge-service/tests/unit/test_normative_legal_extraction.py

"""Регрессии ограниченной атомизации юридических статей.

Проверяет отсутствие потерь исходного текста, сохранение физических
координат, ограничение размера атомов и неизменность режима СП/ГОСТ.
Не обращается к PostgreSQL, Qdrant или внешним моделям.
"""

import json
from uuid import UUID

from pdrd_knowledge_service.domain.normative_indexing import (
    NormativeTextPage,
)
from pdrd_knowledge_service.domain.normative_requirement_extraction import (
    extract_normative_requirements,
)

DOCUMENT_ID = UUID("c418532c-6b29-4fa5-9554-599af833a020")
SHA256 = "a" * 64
MAX_ATOM_BYTES = 2 * 1024 * 1024


def extract(pages):
    """Извлекает тестовые атомы из переданных физических страниц."""
    return tuple(
        extract_normative_requirements(
            document_id=DOCUMENT_ID,
            source_sha256=SHA256,
            source_file="legal-test.pdf",
            pages=tuple(pages),
        )
    )


def unique_sources(rows):
    """Восстанавливает последовательность источников без повторов атомов."""
    seen = set()
    sources = []

    for row in rows:
        first = row.source_fragments[0]
        key = (
            first.page,
            first.start,
            row.source_text,
        )

        if key not in seen:
            seen.add(key)
            sources.append(row.source_text)

    return sources


def test_legal_articles_have_distinct_boundaries():
    """Статьи разделяются, но не считаются автоматически проверенными."""
    pages = (
        NormativeTextPage(
            1,
            "Земельный кодекс\n"
            "Статья 1. Общие положения\n"
            "Земли должны использоваться по назначению.\n"
            "Статья 2. Порядок применения\n"
            "При определённых условиях следует выполнить проверку.",
        ),
    )

    rows = extract(pages)

    assert rows
    assert {"1", "2"} <= {row.clause for row in rows}
    assert all(row.extraction_status == "needs_review" for row in rows)
    assert all("legal_article_requires_review" in row.limitations for row in rows)

    assert "".join(unique_sources(rows)) == pages[0].text


def test_large_unrecognized_article_region_is_split_without_text_loss():
    """Большой пробел между заголовками статей не становится огромным атомом."""
    pages = [
        NormativeTextPage(
            1,
            "Земельный кодекс\n"
            "Статья 1. Основные положения.\n" + "Описание условий. " * 250,
        )
    ]

    for page_number in range(2, 100):
        pages.append(
            NormativeTextPage(
                page_number,
                "Продолжение положения. " * 210,
            )
        )

    pages.append(
        NormativeTextPage(
            100,
            "Статья 2. Заключительные положения.\n"
            "Следует учитывать установленные условия.",
        )
    )

    rows = extract(pages)
    source = "\n".join(page.text for page in pages)

    assert rows
    assert "".join(unique_sources(rows)) == source

    assert any("source_segment_requires_review" in row.limitations for row in rows)

    assert any(
        row.clause == "" and "clause_binding_unavailable" in row.limitations
        for row in rows
    )

    assert all(len(row.source_text) <= 8000 for row in rows)

    assert all(
        len(
            json.dumps(
                row.as_dict(),
                allow_nan=False,
            ).encode("utf-8")
        )
        <= MAX_ATOM_BYTES
        for row in rows
    )

    for row in rows:
        for fragment in row.source_fragments:
            page = pages[fragment.page - 1]
            assert page.text[fragment.start : fragment.end] == fragment.text


def test_existing_sp_clause_extraction_is_preserved():
    """Обычные нумерованные пункты СП не переходят в юридический режим."""
    pages = (
        NormativeTextPage(
            1,
            "СП 99.13330.2026\n"
            "5.1 Для насоса следует предусматривать резерв.\n"
            "5.2 Требуется выполнить расчёт.",
        ),
    )

    rows = extract(pages)
    clauses = {row.clause for row in rows}

    assert {"5.1", "5.2"} <= clauses

    assert all("legal_article_requires_review" not in row.limitations for row in rows)

    assert all("source_segment_requires_review" not in row.limitations for row in rows)


def test_legal_atom_ids_are_deterministic():
    """Повтор извлечения не меняет идентификаторы атомов."""
    pages = (
        NormativeTextPage(
            1,
            "Статья 1. Земли должны использоваться по назначению.\n"
            "Статья 2. Следует соблюдать установленные ограничения.",
        ),
    )

    first = extract(pages)
    second = extract(pages)

    assert first == second
    assert len({row.requirement_id for row in first}) == len(first)
