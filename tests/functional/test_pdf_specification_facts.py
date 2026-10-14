# tests/functional/test_pdf_specification_facts.py

"""Контрольный PDF: один источник обслуживает базовые сведения, D и EQ без переписывания VLM."""

from dataclasses import asdict
from pathlib import Path

import pytest
from pdrd_analysis_service.application.use_cases.understanding import UnderstandPage
from pdrd_analysis_service.domain.analysis import GenerationMetrics, GenerationResult
from pdrd_analysis_service.domain.document_context import (
    LocatedDocumentFact,
    discover_conflicts,
)
from pdrd_analysis_service.transport.http.schemas import PageFactsPayload
from pdrd_document_service.infrastructure.pdf.pymupdf import PyMuPdfReader


@pytest.fixture(scope="module")
def pages():
    """Извлекает действительный fixture один раз, сохраняя все физические листы."""
    path = Path(__file__).parents[1] / "fixtures/equipment/ios-4-1-7.pdf"
    return (
        PyMuPdfReader(render_max_side=1000, text_limit=12000)
        .extract(
            path.read_bytes(),
            selected_pages=tuple(range(1, 32)),
            include_equipment_specification=True,
        )
        .pages
    )


class NoTableVision:
    """Основной вызов для таблицы запрещён; короткое неуспешное уточнение не меняет исходник."""

    def __init__(self):
        """Записывает разрешённые короткие уточнения."""
        self.calls = []

    async def generate_json(self, **request):
        """Отделяет уточнение нескольких строк от повторного извлечения всего листа."""
        self.calls.append(request["stage"])
        assert request["stage"].startswith("specification_rows:")
        return GenerationResult(
            {"items": []}, GenerationMetrics(1, "stop", 2400, 1, 0, 10, 10, 10, 0)
        )


@pytest.mark.parametrize("d,eq", [(True, True), (True, False), (False, True)])
async def test_real_255_rows_and_131_positions_are_programmatic_and_complete(
    pages, d, eq
):
    """Количество и происхождение не зависят от старого ограничения 12 VLM-фактов."""
    model = NoTableVision()
    original = [page for page in pages if page.equipment_specification]
    assert [page.number for page in original] == list(range(24, 32))
    assert (
        sum(page.equipment_specification["raw_row_count"] for page in original) == 255
    )
    assert sum(len(page.equipment_specification["items"]) for page in original) == 131
    outputs, located = [], []
    for page in original:
        facts, metrics = await UnderstandPage(model, 4000).execute(
            page_number=page.number,
            heuristic_page_type=page.page_type,
            extracted_text=page.text,
            image_bytes=page.rendered_png,
            equipment_specification=page.equipment_specification,
            use_document_context=d,
            use_equipment_web_search=eq,
        )
        assert metrics.as_dict()["understanding"]["route"] == "pdf_table"
        assert metrics.as_dict()["understanding"]["vlm_facts"] == 0
        assert (
            facts.equipment_specification["raw_rows"]
            == page.equipment_specification["raw_rows"]
        )
        for saved, source in zip(
            facts.equipment_specification["items"],
            page.equipment_specification["items"],
            strict=True,
        ):
            for field in (
                "item_id",
                "model",
                "manufacturer",
                "variant",
                "quantity",
                "units",
                "source_pages",
                "row_ids",
                "source_regions",
                "Caption",
                "Units",
                "Quantity",
                "UserComment1",
                "Under",
            ):
                assert saved[field] == source[field]
        assert len(facts.equipment_identities) == (
            len(page.equipment_specification["items"]) if eq else 0
        )
        assert bool(facts.document_facts) is d
        if d and page.number == 25:
            assert len(facts.document_facts) > 12
        for index, fact in enumerate(facts.document_facts):
            item = next(
                item
                for item in facts.equipment_specification["items"]
                if item["item_id"] == fact.source_item_id
            )
            assert fact.source_row_ids == tuple(item["row_ids"])
            assert fact.source_pages == tuple(item["source_pages"])
            assert fact.source_regions and fact.visual_regions and fact.evidence_text
            assert fact.identifier == item["item_id"]
            located.append(LocatedDocumentFact(page.number, index, fact))
        roundtrip = PageFactsPayload.model_validate(asdict(facts)).to_domain()
        assert roundtrip.document_facts == facts.document_facts
        outputs.append(facts)
    assert all(not stage.startswith("page_understanding:") for stage in model.calls)
    assert discover_conflicts(tuple(located), max_candidates=24) == ()
    assert sum(len(facts.equipment_specification["items"]) for facts in outputs) == 131
