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
from pdrd_document_service.transport.http.schemas.pdf_annotation import (
    PdfFindingAnnotationRequest,
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
                content="Замечание: Нет маркировки ЩР-1.",
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
                content="Замечание на повёрнутом листе.",
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
                            value="Нет маркировки ЩР-1.",
                        ),
                        PdfReportField(
                            label="Рекомендация",
                            value="Добавить маркировку.",
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


def test_annotation_schema_accepts_empty_regions_for_page_level_marker() -> None:
    """Transport допускает page-level finding без выдуманного bbox."""
    request = PdfFindingAnnotationRequest(
        number=7,
        finding_id="F-7",
        page_number=2,
        title="Замечание №7",
        content="Точное место не локализовано.",
        regions=[],
    )

    annotation = request.to_domain()

    assert annotation.page_number == 2
    assert annotation.regions == ()


def test_writer_adds_page_level_marker_when_bbox_is_unavailable() -> None:
    """Unlocated finding остаётся видимым на исходном PDF-листе."""
    writer = PyMuPdfAnnotationWriter()

    result = writer.build(
        content=_source_pdf(),
        annotations=(
            PdfFindingAnnotation(
                number=3,
                finding_id="F-3",
                page_number=1,
                title="Замечание №3",
                content=(
                    "Точное место автоматически не локализовано. "
                    "Проверить лист целиком."
                ),
                regions=(),
            ),
        ),
        report=PdfTextReport(
            title="Отчёт анализа PDRD",
            metadata=(),
            summary="",
            findings=(),
            limitations=(),
        ),
    )

    document = fitz.open(
        stream=result,
        filetype="pdf",
    )

    try:
        page = document[0]

        annotations = list(page.annots() or ())

        assert (
            len(
                annotations,
            )
            == 2
        )

        free_text = next(
            annotation for annotation in annotations if annotation.type[1] == "FreeText"
        )

        comment = next(
            annotation for annotation in annotations if annotation.type[1] == "Text"
        )

        assert free_text.info["content"] == "PDRD [3]"
        assert free_text.info["title"] == "Замечание №3"
        assert comment.info["name"] == "Comment"

        assert "Точное место автоматически не локализовано." in comment.info["content"]

        assert free_text.rect.is_empty is False

        assert (free_text.rect & page.rect).is_empty is False

    finally:
        document.close()
