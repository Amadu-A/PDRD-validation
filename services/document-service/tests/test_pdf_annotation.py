# services/document-service/tests/test_pdf_annotation.py

"""Tests native PyMuPDF annotation export."""

import fitz
from pdrd_document_service.application.ports.pdf_annotation import (
    PdfFindingAnnotation,
    PdfReportField,
    PdfReportFinding,
    PdfTextReport,
)
from pdrd_document_service.domain.pdf import (
    PdfNormalizedBoundingBox,
)
from pdrd_document_service.infrastructure.pdf.annotator import (
    PyMuPdfAnnotationWriter,
)


def _source_pdf() -> bytes:
    """Создаёт synthetic two-page PDF, включая rotated page."""
    document = fitz.open()

    first_page = document.new_page(
        width=595,
        height=842,
    )

    first_page.insert_text(
        (
            72,
            72,
        ),
        "ORIGINAL PAGE 1",
    )

    second_page = document.new_page(
        width=842,
        height=595,
    )

    second_page.insert_text(
        (
            72,
            72,
        ),
        "ORIGINAL PAGE 2",
    )

    second_page.set_rotation(
        90,
    )

    content = document.tobytes()

    document.close()

    return content


def test_writer_preserves_original_pages_and_appends_report() -> None:
    """Export сохраняет исходный PDF, annotations и русский report."""
    writer = PyMuPdfAnnotationWriter()

    result = writer.build(
        content=_source_pdf(),
        annotations=(
            PdfFindingAnnotation(
                number=1,
                finding_id="F-1",
                page_number=1,
                title="Замечание №1",
                content=("Замечание: Нет маркировки ЩР-1."),
                regions=(
                    PdfNormalizedBoundingBox(
                        x_min=100,
                        y_min=100,
                        x_max=300,
                        y_max=180,
                    ),
                ),
            ),
            PdfFindingAnnotation(
                number=2,
                finding_id="F-2",
                page_number=2,
                title="Замечание №2",
                content=("Замечание на повёрнутом листе."),
                regions=(
                    PdfNormalizedBoundingBox(
                        x_min=200,
                        y_min=200,
                        x_max=400,
                        y_max=300,
                    ),
                ),
            ),
        ),
        report=PdfTextReport(
            title="Отчёт анализа PDRD",
            metadata=(
                PdfReportField(
                    label="PDF",
                    value="drawing.pdf",
                ),
            ),
            summary="Итоговая сводка.",
            findings=(
                PdfReportFinding(
                    title="1. Лист/страница 1",
                    fields=(
                        PdfReportField(
                            label="Замечание",
                            value=("Нет маркировки ЩР-1."),
                        ),
                        PdfReportField(
                            label="Рекомендация",
                            value=("Добавить маркировку."),
                        ),
                    ),
                ),
            ),
            limitations=("Требуется инженерная проверка.",),
        ),
    )

    assert result.startswith(
        b"%PDF-",
    )

    document = fitz.open(
        stream=result,
        filetype="pdf",
    )

    try:
        assert document.page_count >= 3

        assert "ORIGINAL PAGE 1" in document[0].get_text()

        assert "ORIGINAL PAGE 2" in document[1].get_text()

        first_page = document[0]

        second_page = document[1]

        first_annotations = list(first_page.annots() or ())

        second_annotations = list(second_page.annots() or ())

        assert (
            len(
                first_annotations,
            )
            == 2
        )

        assert (
            len(
                second_annotations,
            )
            == 2
        )

        assert first_annotations[0].info["title"] == "Замечание №1"

        report_text = "\n".join(
            document[page_index].get_text()
            for page_index in range(
                2,
                document.page_count,
            )
        )

        assert "Отчёт анализа PDRD" in report_text

        assert "Нет маркировки ЩР-1." in report_text

        assert "Требуется инженерная проверка." in report_text

    finally:
        document.close()
