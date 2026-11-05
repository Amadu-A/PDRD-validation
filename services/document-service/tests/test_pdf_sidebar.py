# services/document-service/tests/test_pdf_sidebar.py

"""Экспорт вправо: пиксели источника, повороты, боксы, доказательства и переполнение."""

from itertools import combinations

import fitz
import pytest
from pdrd_document_service.application.ports.pdf_annotation import (
    PdfFindingAnnotation,
    PdfReportField,
    PdfReportFinding,
    PdfTextReport,
)
from pdrd_document_service.domain.pdf import PdfNormalizedBoundingBox
from pdrd_document_service.infrastructure.pdf.annotator import PyMuPdfAnnotationWriter


@pytest.mark.parametrize("size", [(595, 842), (842, 1191), (1191, 1684), (1684, 2384)])
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_sidebar_preserves_source_pixels_and_native_regions(size, rotation):
    """A4/A3/A2/A1 с ненулевыми Media/CropBox сохраняют физический фрагмент."""
    width, height = size
    with fitz.open() as source:
        page = source.new_page(width=width, height=height)
        page.insert_text((100, 150), "ORIGINAL DRAWING", fontsize=20)
        page.draw_rect(fitz.Rect(80, 180, 260, 280), color=(0, 0, 0))
        source.xref_set_key(
            page.xref, "MediaBox", f"[20 30 {width + 20} {height + 30}]"
        )
        source.xref_set_key(page.xref, "CropBox", f"[40 60 {width - 40} {height - 40}]")
        page = source.reload_page(page)
        page.set_rotation(rotation)
        original_rect = fitz.Rect(page.rect)
        before = page.get_pixmap(annots=False)
        original_text = page.get_text()
        # Нативная аннотация исходного чертежа также должна сохраниться.
        native = page.add_text_annot((200, 200), "SOURCE NOTE", icon="Note")
        native.set_info(title="Original annotation")
        content = source.tobytes()
    bbox = PdfNormalizedBoundingBox(100.25, 200.5, 300.75, 350.25)
    manual_box = PdfNormalizedBoundingBox(400, 400, 700, 500)
    annotations = tuple(
        PdfFindingAnnotation(
            i,
            f"id-{i}",
            1,
            "Проверить диаметр трубопровода и расчётное обоснование. D-p0026-f0012",
            "Технический полный текст",
            (bbox,),
            callout_box=manual_box if i == 2 else None,
        )
        for i in (1, 2)
    )
    result = PyMuPdfAnnotationWriter().build(
        content=content,
        annotations=annotations,
        report=PdfTextReport(
            "Контроль",
            (),
            "",
            (
                PdfReportFinding(
                    "Замечание", (PdfReportField("Текст", "ПОЛНЫЙ ТЕКСТ ИСТОЧНИКА"),)
                ),
            ),
            (),
        ),
    )
    with fitz.open(stream=result, filetype="pdf") as exported:
        page = exported[0]
        assert page.rect.width > original_rect.width
        assert page.rect.height == original_rect.height
        assert (
            before.samples == page.get_pixmap(clip=original_rect, annots=False).samples
        )
        assert original_text.strip() in page.get_text()
        notes = list(page.annots())
        assert any(note.info["title"] == "Original annotation" for note in notes)
        owned = [note for note in notes if note.info["title"].startswith("Замечание №")]
        assert not any(note.type[1] == "Text" for note in owned)
        for number in (1, 2):
            own = [
                note for note in owned if note.info["title"] == f"Замечание №{number}"
            ]
            squares = [note for note in own if note.type[1] == "Square"]
            region = squares[0].rect * page.rotation_matrix
            expected = [
                original_rect.width * bbox.x_min / 1000,
                original_rect.height * bbox.y_min / 1000,
                original_rect.width * bbox.x_max / 1000,
                original_rect.height * bbox.y_max / 1000,
            ]
            assert list(region) == pytest.approx(expected, abs=1.3)
            card = squares[1].rect * page.rotation_matrix
            assert card.x0 > original_rect.width
            line = next(note for note in own if note.type[1] == "Line")
            start, end = [
                fitz.Point(point) * page.rotation_matrix for point in line.vertices
            ]
            assert region.contains(start)
            assert card.contains(end)
            assert line.colors["stroke"] == pytest.approx((0.86, 0.12, 0.12))
            assert all(
                not note.info["content"]
                for note in own
                if note.type[1] in {"Square", "Line"}
            )
            text = next(
                note
                for note in own
                if note.type[1] == "FreeText" and "Проверить" in note.get_text()
            )
            visible = " ".join(text.get_text().split())
            assert "D-p0026" not in visible
            assert "Полный текст и основания" in visible
            fonts = {
                span["size"]
                for block in text.get_text("dict")["blocks"]
                for ln in block.get("lines", [])
                for span in ln["spans"]
            }
            old_font = max(7, min(20, original_rect.width / 120))
            if number == 2:
                from pdrd_document_service.infrastructure.pdf.review_style import (
                    pinned_font_size,
                )

                old_font = pinned_font_size(
                    original_rect.width * 0.3, original_rect.height * 0.1, old_font
                )
            assert fonts and all(abs(font - old_font - 1.5) < 0.01 for font in fonts)
        assert annotations[1].callout_box == manual_box
        assert "ПОЛНЫЙ ТЕКСТ ИСТОЧНИКА" in exported[1].get_text()


def test_many_long_findings_add_columns_without_losing_cards_or_lines():
    """Переполнение не уменьшает шрифт и не удаляет независимые замечания."""
    with fitz.open() as source:
        source.new_page(width=595, height=842)
        content = source.tobytes()
    annotations = tuple(
        PdfFindingAnnotation(
            i,
            f"id-{i}",
            1,
            "Длинный содержательный вывод. " * 60,
            f"Полное доказательство {i}",
            (PdfNormalizedBoundingBox(100, 200, 250, 300),),
        )
        for i in range(1, 31)
    )
    report = PdfTextReport(
        "Проверка переполнения",
        (),
        "",
        tuple(
            PdfReportFinding(
                f"Замечание №{i}",
                (PdfReportField("Основание", f"Полное доказательство {i}"),),
            )
            for i in range(1, 31)
        ),
        (),
    )
    result = PyMuPdfAnnotationWriter().build(
        content=content, annotations=annotations, report=report
    )
    with fitz.open(stream=result, filetype="pdf") as document:
        page = document[0]
        notes = list(page.annots())
        cards = [
            note.rect
            for note in notes
            if note.type[1] == "Square" and note.rect.x0 > 595
        ]
        assert len(cards) == 30
        assert len([note for note in notes if note.type[1] == "Line"]) == 30
        assert all(not a.intersects(b) for a, b in combinations(cards, 2))
        assert all(page.rect.contains(card) for card in cards)
        assert page.rect.width > 595 + 2 * 220
        text = " ".join(document[i].get_text() for i in range(1, len(document)))
        assert all(f"Полное доказательство {i}" in text for i in range(1, 31))
