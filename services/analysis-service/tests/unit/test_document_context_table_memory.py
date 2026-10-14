# services/analysis-service/tests/unit/test_document_context_table_memory.py

"""Рост программных D-фактов не создаёт квадратичную копию общей таблицы в retrieval."""

import tracemalloc
from dataclasses import replace

from pdrd_analysis_service.application.use_cases.document_context import (
    DocumentPage,
    build_page_document_context,
)
from pdrd_analysis_service.domain.specification_facts import (
    specification_document_facts,
)

from .test_document_context import _options
from .test_equipment_specification import specification


def test_large_table_retrieval_keeps_bounded_memory_and_source_limit():
    """1200 фактов общей таблицы остаются доступны; промежуточная память ограничена 6 МБ."""
    facts = tuple(
        replace(fact, table_id="table-1")
        for fact in specification_document_facts(specification(300), 25)
    )
    pages = (
        DocumentPage(25, "Таблица", "Таблица", facts),
        DocumentPage(31, "Продолжение", "Таблица", facts[:20]),
    )
    tracemalloc.start()
    try:
        result = build_page_document_context(
            pages=pages, semantic_by_page={}, options=_options()
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(facts) == 1200
    assert len(result[25]) == len(result[31]) == 3
    assert all(source.page == 31 for source in result[25])
    assert peak < 6 * 1024 * 1024
