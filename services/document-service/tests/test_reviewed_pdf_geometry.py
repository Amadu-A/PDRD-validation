# services/document-service/tests/test_reviewed_pdf_geometry.py

"""Физическая геометрия Gold и полный текст на повёрнутых и обрезанных листах."""

import fitz
import pytest
from pdrd_document_service.application.ports.pdf_annotation import (
    PdfReportField,
    PdfReportFinding,
    PdfTextReport,
)
from pdrd_document_service.infrastructure.pdf.annotator import PyMuPdfAnnotationWriter
from pdrd_document_service.transport.http.schemas.pdf_annotation import (
    PdfFindingAnnotationRequest,
)

BOX = {"x_min": 110.25, "y_min": 210.75, "x_max": 300.5, "y_max": 380.125}
CARD = {"x_min": 450.5, "y_min": 190.75, "x_max": 920.25, "y_max": 400.5}
TEXT = "Пользователь обнаружил отсутствующее обозначение. "


def reviewed_pdf(*, rotation=0, cropped=False, text=TEXT):
    """Строит реальный PDF с заданной Gold-карточкой и русским текстовым приложением."""
    with fitz.open() as original:
        page = original.new_page(width=842, height=595)
        page.draw_rect(fitz.Rect(80, 100, 300, 250), color=(0, 0, 0))
        page.insert_text((80, 80), "ORIGINAL DRAWING")
        if cropped:
            page.set_cropbox(fitz.Rect(50, 40, 800, 560))
        page.set_rotation(rotation)
        source = original.tobytes()
    annotation = PdfFindingAnnotationRequest(
        number=1,
        finding_id="manual:1",
        page_number=1,
        title=text,
        content=text,
        origin="manual",
        regions=[BOX],
        callout_box=CARD,
    ).to_domain()
    report = PdfTextReport(
        title="Итоговый PDF после Human Review",
        metadata=(),
        summary="Принято замечаний: 1.",
        findings=(
            PdfReportFinding(
                "Замечание №1 · Gold",
                (PdfReportField("Замечание", text),),
                origin="manual",
            ),
        ),
        limitations=(),
    )
    return PyMuPdfAnnotationWriter().build(
        content=source, annotations=(annotation,), report=report
    )


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("cropped", [False, True])
def test_gold_preserves_both_fractional_rectangles(rotation, cropped):
    """Деротация сохраняет обе выбранные области, цвет и соединитель."""
    with fitz.open(
        stream=reviewed_pdf(rotation=rotation, cropped=cropped), filetype="pdf"
    ) as document:
        page = document[0]
        squares = [note for note in page.annots() if note.type[1] == "Square"]
        assert len(squares) == 2
        for note, box in zip(squares, (BOX, CARD), strict=True):
            visual = note.rect * page.rotation_matrix
            expected = [
                page.rect.width * box["x_min"] / 1000,
                page.rect.height * box["y_min"] / 1000,
                page.rect.width * box["x_max"] / 1000,
                page.rect.height * box["y_max"] / 1000,
            ]
            assert list(visual) == pytest.approx(expected, abs=1.3)
            assert note.colors["stroke"] == pytest.approx((0.65, 0.49, 0.08))
        assert len([note for note in page.annots() if note.type[1] == "Line"]) == 1
        help_note = next(note for note in page.annots() if note.type[1] == "Text")
        assert help_note.info["content"] == TEXT
        assert "Пользователь обнаружил" in document[1].get_text()


def test_long_full_text_is_preserved_across_report_pages():
    """Карточка сокращается визуально, полный текст Help и приложения не обрезается."""
    text = TEXT * 170 + "КОНЕЦ ПОЛНОГО ЗАМЕЧАНИЯ"
    with fitz.open(stream=reviewed_pdf(text=text), filetype="pdf") as document:
        page = document[0]
        assert (
            next(note for note in page.annots() if note.type[1] == "Text").info[
                "content"
            ]
            == text
        )
        assert len(document) >= 3
        report = " ".join(
            " ".join(document[i].get_text() for i in range(1, len(document))).split()
        )
        assert report.count("Пользователь") == 170
        assert "КОНЕЦ ПОЛНОГО ЗАМЕЧАНИЯ" in report
