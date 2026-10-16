# tests/functional/test_decision_registry_pdf.py

"""Реальный PDF: все программные факты спецификации сохраняются в реестре без VLM."""

from dataclasses import asdict
from pathlib import Path

from pdrd_analysis_service.domain.design_decisions import build_decision_registry
from pdrd_analysis_service.domain.document_context import LocatedDocumentFact
from pdrd_analysis_service.domain.specification_facts import (
    specification_document_facts,
)
from pdrd_document_service.infrastructure.pdf.pymupdf import PyMuPdfReader


def test_real_specification_positions_rows_units_and_geometry_are_not_lost():
    """255 исходных строк и 131 позиция проходят прежний reader и новый реестр."""
    path = Path(__file__).parents[1] / "fixtures/equipment/ios-4-1-7.pdf"
    pages = (
        PyMuPdfReader(render_max_side=300, text_limit=12000)
        .extract(
            path.read_bytes(),
            selected_pages=tuple(range(1, 32)),
            include_equipment_specification=True,
        )
        .pages
    )
    tables = [page for page in pages if page.equipment_specification]
    assert sum(page.equipment_specification["raw_row_count"] for page in tables) == 255
    assert sum(len(page.equipment_specification["items"]) for page in tables) == 131
    facts = tuple(
        LocatedDocumentFact(page.number, index, fact)
        for page in tables
        for index, fact in enumerate(
            specification_document_facts(page.equipment_specification, page.number), 1
        )
    )
    registry = build_decision_registry(
        document_id="independent-specification",
        facts=facts,
        page_texts={page.number: page.text for page in pages},
    )
    assert len(registry.evidence) == len(facts) > 500
    assert [asdict(item.observation) for item in registry.evidence] == [
        asdict(item) for item in facts
    ]
    assert {item.observation.fact.source_item_id for item in registry.evidence} == {
        item["item_id"]
        for page in tables
        for item in page.equipment_specification["items"]
    }
    assert {item.observation.page for item in registry.evidence} == set(range(24, 32))
    assert all(item.observation.fact.source_regions for item in registry.evidence)
    assert all(
        table["completeness"] == "ambiguous" for table in registry.table_observations()
    )
