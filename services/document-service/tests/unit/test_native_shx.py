# services/document-service/tests/unit/test_native_shx.py

"""SHX-подписи исходного PDF читаются с геометрией без OCR и без замечаний Review."""

import fitz
import pytest
from pdrd_document_service.domain.specification import project_code_from_text
from pdrd_document_service.domain.specification_geometry import bind_pdf_row
from pdrd_document_service.infrastructure.pdf.pymupdf import PyMuPdfReader


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_native_shx_with_rotation_preserves_text_and_regions_and_excludes_review(
    rotation,
):
    """Четыре поворота сохраняют исходную подпись; Gold не превращается в проектный факт."""
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        annotation = page.add_rect_annot(fitz.Rect(100, 200, 220, 230))
        annotation.set_info(
            title="AutoCAD SHX Text", content="Труба ПЭ100 SDR11 110x10"
        )
        annotation.update()
        annotation = page.add_rect_annot(fitz.Rect(300, 400, 450, 450))
        annotation.set_info(
            title="Замечание №1", content="Искусственное замечание пользователя"
        )
        annotation.update()
        page.set_rotation(rotation)
        source = document.tobytes()
    page = (
        PyMuPdfReader(render_max_side=600, text_limit=12000)
        .extract(source, selected_pages=(1,))
        .pages[0]
    )
    assert "Труба ПЭ100 SDR11 110x10" in page.text
    assert "Искусственное" not in page.text
    spans = [word for word in page.text_words if "Труба" in word.text]
    assert len(spans) == 1
    box = spans[0].bbox
    assert 0 <= box.x_min < box.x_max <= 1000 and 0 <= box.y_min < box.y_max <= 1000
    # Центр исходной подписи вычислен независимо для четырёх визуальных систем.
    centers = {0: (267, 269), 90: (731, 267), 180: (733, 731), 270: (269, 733)}
    x, y = centers[rotation]
    assert box.x_min <= x <= box.x_max and box.y_min <= y <= box.y_max


def test_native_layer_and_equal_shx_are_not_counted_twice():
    """Дубликат того же текстового слоя в той же области не создаёт второй объект."""
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "PIPE110")
        rect = fitz.Rect(page.get_text("words")[0][:4])
        annotation = page.add_rect_annot(
            fitz.Rect(rect.x0 - 1, rect.y0 - 1, rect.x1 + 1, rect.y1 + 1)
        )
        annotation.set_info(title="AutoCAD SHX Text", content="PIPE110")
        annotation.update()
        source = document.tobytes()
    page = (
        PyMuPdfReader(render_max_side=500, text_limit=12000)
        .extract(source, selected_pages=(1,))
        .pages[0]
    )
    assert page.text.count("PIPE110") == 1
    assert sum(word.text == "PIPE110" for word in page.text_words) == 1


def test_explicit_object_code_is_fallback_only_for_ambiguous_shx_document_code():
    """Уже поддержанный шифр документа сохраняется, противоречащие явные шифры не скрываются."""
    assert (
        project_code_from_text("120/155-16-ИОС4.1.7; шифр Объекта 120/155")
        == "120/155-16-ИОС4.1.7"
    )
    assert (
        project_code_from_text(
            "777/155-1с-ИОС2.2.2Сс-ИОС2.2.2; (шифр Объекта 777/155-1с)"
        )
        == "777/155-1с"
    )
    assert (
        project_code_from_text("шифр проекта:A-1; шифр проекта:B-2; шифр объекта C-3")
        == ""
    )


def span(text, x0, x1, y0=100, y1=120):
    """Задаёт буквальную подпись и её собственный bbox, без интерполяции строки."""
    return {"text": text, "x_min": x0, "x_max": x1, "y_min": y0, "y_max": y1}


def test_geometry_requires_same_row_quantity_units_and_exact_size():
    """Количество соседнего объекта или числовой префикс не доказывают привязку."""
    item = {
        "Caption": "Труба ПЭ100 110x10",
        "Quantity": "5",
        "Units": "м",
        "Brand": "",
        "Article": "",
    }
    source = {
        "native_spans": [
            span(item["Caption"], 100, 400),
            span("м", 700, 720),
            span("5", 800, 820),
        ],
        "graphic_lines": [
            {"x1": 50, "x2": 950, "y1": y, "y2": y} for y in [90, 130, 170]
        ],
    }
    assert bind_pdf_row(item, source) == [
        {key: value for key, value in s.items() if key != "text"}
        for s in source["native_spans"]
    ]
    assert bind_pdf_row({**item, "Quantity": "50"}, source) == []
    assert (
        bind_pdf_row(
            item,
            {
                **source,
                "native_spans": [
                    span("Труба ПЭ100 110x100", 100, 400),
                    *source["native_spans"][1:],
                ],
            },
        )
        == []
    )
    assert (
        bind_pdf_row(
            item,
            {
                **source,
                "native_spans": [
                    *source["native_spans"][:2],
                    span("5", 800, 820, 140, 160),
                ],
            },
        )
        == []
    )
    assert bind_pdf_row(item, {**source, "graphic_lines": []}) == []
