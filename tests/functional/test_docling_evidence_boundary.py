# tests/functional/test_docling_evidence_boundary.py

"""Сквозной контракт OCR: весь текст доступен анализу, но не подменяет нативное доказательство."""

from dataclasses import replace

import pytest
from pdrd_analysis_service.application.use_cases.duplicate_positions import (
    repeated_standalone_positions,
)
from pdrd_analysis_service.domain.design_decisions import build_decision_registry
from pdrd_analysis_service.domain.document_context import (
    LocatedDocumentFact,
    document_fact_from_mapping,
)
from pdrd_analysis_service.domain.engineering_checks import program_evidence_eligible
from pdrd_analysis_service.domain.evidence_audit import bind_evidence
from pdrd_document_service.application.use_cases.enrich_document import (
    EnrichDocument,
    reuse_converted_project_context,
)
from pdrd_document_service.domain.pdf import PdfDocument, PdfPage, PdfPageType
from pdrd_document_service.domain.project_context import (
    ExplanatoryNoteContext,
    PdfTextPage,
)


@pytest.mark.parametrize("status", ["ready", "fallback"])
def test_context_reuses_only_ready_selected_ocr_and_preserves_native_evidence(status):
    """ПЗ получает уже прочитанный скан, без потери номера, маркера и готового native-текста."""
    text = "[DOCLING_OCR_ALTERNATIVE: OCR]\nВ2: наружный диаметр 90 мм."
    converted = PdfPage(
        1,
        PdfPageType.GENERAL_NOTES,
        text,
        100,
        200,
        b"image",
        conversion_metadata={"provider": "docling", "status": status, "do_ocr": True},
    )
    context = ExplanatoryNoteContext(
        True,
        1,
        3,
        (PdfTextPage(1, ""), PdfTextPage(2, "Нативная запись"), PdfTextPage(3, "")),
    )
    document = PdfDocument(3, (converted, replace(converted, number=2)))
    result = reuse_converted_project_context(document, context)
    assert result.pages_count == 3 and (result.start_page, result.end_page) == (1, 3)
    assert result.pages[0].text == (text if status == "ready" else "")
    assert result.pages[1] == context.pages[1] and result.pages[2] == context.pages[2]
    if status == "ready":
        fact = document_fact_from_mapping(
            {
                "kind": "parameter",
                "subject_type": "system",
                "identifier": "В2",
                "property_type": "outer_diameter",
                "property_name": "Наружный диаметр",
                "value_raw": "90",
                "unit_raw": "мм",
                "evidence_text": "В2: наружный диаметр 90 мм.",
                "extraction_method": "pdf_text",
            }
        )
        assert (
            bind_evidence(
                LocatedDocumentFact(1, 1, fact), {1: result.pages[0].text}
            ).status
            != "supported"
        )
    disabled = ExplanatoryNoteContext.disabled()
    assert reuse_converted_project_context(document, disabled) is disabled


def test_ocr_repetition_does_not_become_protected_native_duplicate():
    """Повтор в альтернативном чтении требует проверки, а не защищённого native-candidate."""
    text = "[DOCLING_OCR_ALTERNATIVE: OCR]\n8.9.1\n8.9.1\n"
    assert repeated_standalone_positions(text, page_type="scheme") == ()


@pytest.mark.parametrize("native", ["", "В2: наружный диаметр 110 мм."])
async def test_docling_reading_cannot_self_confirm_native_quote(native):
    """Ошибка OCR 90 вместо 110 не становится доказанным конфликтом; реальная native-цитата работает."""
    alternative = "В2: наружный диаметр 90 мм."

    class Converter:
        """Возвращает успешное чтение, сохраняемое адаптером отдельно от исходного PDF."""

        async def convert(self, **kwargs):
            """OCR применяется только при отсутствии нативного слоя."""
            assert kwargs["do_ocr"] is (not bool(native))
            return {
                "artifact_key": "a" * 64,
                "result": {
                    "document": {
                        "json_content": {"texts": [{"text": alternative}], "tables": []}
                    }
                },
            }

    page = PdfPage(1, PdfPageType.TABLE, native, 100, 200, b"image")
    document = await EnrichDocument(Converter()).execute(
        document=PdfDocument(1, (page,)), content=b"source"
    )
    text = document.pages[0].text
    assert alternative in text and (not native or text.startswith(native))

    def observation(value, quote, index):
        """Имитирует даже ошибочно названный pdf_text, чтобы проверить саму границу доверия."""
        fact = document_fact_from_mapping(
            {
                "kind": "parameter",
                "subject_type": "system",
                "identifier": "В2",
                "property_type": "outer_diameter",
                "property_name": "Наружный диаметр",
                "value_raw": value,
                "unit_raw": "мм",
                "evidence_text": quote,
                "extraction_method": "pdf_text",
            }
        )
        assert fact is not None
        return LocatedDocumentFact(1, index, fact)

    ocr = observation("90", alternative, 1)
    assert bind_evidence(ocr, {1: text}).status != "supported"
    items = (ocr, observation("110", native, 2)) if native else (ocr,)
    registry = build_decision_registry(
        document_id="ocr-case", facts=items, page_texts={1: text}
    )
    assert len(registry.evidence) == len(items)
    assert not program_evidence_eligible(registry.evidence[0])
    if native:
        assert bind_evidence(items[1], {1: text}).status == "supported"
        assert program_evidence_eligible(registry.evidence[1])
