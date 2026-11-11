# tests/functional/test_native_identifier_context.py

"""Полный native-слой дополняет контекст без расширения лимита VLM-фактов."""

from pdrd_analysis_service.application.native_document_context import (
    native_identifier_sources,
)
from pdrd_analysis_service.application.use_cases.document_context import DocumentPage


def test_context_finds_graphic_inventory_even_when_understanding_omitted_every_fact():
    """Обозначение сети приводит к схеме и спецификации с реальным составом позиций."""
    pages = (
        DocumentPage(8, "Для водоснабжения В2 предусмотрен гидрант.", "", ()),
        DocumentPage(9, "Расход В2 принят согласно расчёту.", "", ()),
        DocumentPage(25, "Принципиальная схема\nB2\nПГ1 ПГ2 ПГ3 ПГ4 ПГ5", "", ()),
        DocumentPage(26, "Спецификация оборудования\nВ2\nПГ1 ПГ2 ПГ3 ПГ4 ПГ5", "", ()),
    )
    sources = native_identifier_sources(pages)[8]
    assert {s["page"] for s in sources} == {25, 26}
    assert all("ПГ5" in s["text"] for s in sources)
    assert all(s["source_id"].startswith("D-") for s in sources)
    assert pages[2].facts == ()


def test_identifier_substrings_and_other_jobs_are_not_context_matches():
    """В2 и В20 различаются; набор другого документа не участвует в поиске."""
    pages = (
        DocumentPage(1, "Система В2", "", ()),
        DocumentPage(2, "Система В20", "", ()),
    )
    assert native_identifier_sources(pages) == {1: (), 2: ()}
    assert native_identifier_sources((pages[0],)) == {1: ()}


def test_native_context_has_bounded_size_and_does_not_rewrite_source_labels():
    """Поисковый псевдоним не меняет подпись и не становится физической идентичностью."""
    pages = (
        DocumentPage(1, "В2", "", ()),
        DocumentPage(15, "Запись " * 600 + "B2 — резервный ввод", "", ()),
    )
    source = native_identifier_sources(pages)[1][0]
    assert len(source["text"]) <= 2400
    assert "B2" in source["text"] and source["subject"] == "B2"
    assert source["fact_id"] is None
