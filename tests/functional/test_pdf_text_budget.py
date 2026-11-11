# tests/functional/test_pdf_text_budget.py

"""Отступы широких листов не вытесняют текст, а слово и bbox остаются неизменными."""

import fitz
from pdrd_analysis_service.application.prompts import (
    build_page_understanding_prompt,
)
from pdrd_analysis_service.application.use_cases.duplicate_positions import (
    repeated_standalone_positions,
)
from pdrd_document_service.infrastructure.pdf.pymupdf import PyMuPdfReader


def test_wide_pdf_keeps_tail_text_and_native_word_geometry_before_budget():
    """Воспроизводит обрезку по пробелам реальным широким PDF без VLM и эталонных ответов."""
    document = fitz.open()
    page = document.new_page(width=1900, height=900)
    for row in range(24):
        page.insert_text((20, 30 + row * 25), f"Row {row}", fontsize=10)
        page.insert_text((1700, 30 + row * 25), f"Column {row}", fontsize=10)
    page.insert_text((20, 800), "TAIL-REFERENCE-735", fontsize=10)
    raw = page.get_text("text", sort=True)
    assert "TAIL-REFERENCE-735" not in raw[:1200]
    reader = PyMuPdfReader(render_max_side=300, text_limit=1200)
    extracted = reader.extract(document.tobytes(), selected_pages=(1,)).pages[0]
    assert "TAIL-REFERENCE-735" in extracted.text
    assert (
        "TAIL-REFERENCE-735"
        in reader.extract_text(document.tobytes(), selected_pages=(1,))[0].text
    )
    actual_words = reader._extract_text_words(page)
    assert extracted.text_words == actual_words
    assert extracted.rendered_png == reader._render_page(page)


def test_understanding_prompt_keeps_all_native_tail_records_without_padding():
    """Старый префикс 8000 символов не должен скрывать основную надпись и реквизиты."""
    text = ("Параметр" + " " * 900 + "значение\n") * 20 + "Конечная подпись: ИОС7-ГЧ"
    prompt = build_page_understanding_prompt(
        page_number=4, heuristic_page_type="drawing", extracted_text=text
    )
    assert "Конечная подпись: ИОС7-ГЧ" in prompt
    assert " " * 900 not in prompt
    assert prompt.count("Параметр значение") == 20


def test_compacted_pdf_keeps_isolated_position_labels_and_ordinary_references():
    """Уплотнение PDF сохраняет повторы подписей, не превращая ссылки в позиции."""
    document = fitz.open()
    page = document.new_page(width=1200, height=500)
    for row in range(2):
        for column, tag in enumerate(("8.9.1", "8.9.2", "8.9.3")):
            page.insert_text((40 + column * 400, 40 + row * 80), tag, fontsize=10)
    page.insert_text((40, 300), "Reference to 8.9.4 in prose.", fontsize=10)
    page.insert_text((40, 330), "Another reference to 8.9.4 in prose.", fontsize=10)
    raw = page.get_text("text", sort=True)
    expected = (("8.9.1", 2), ("8.9.2", 2), ("8.9.3", 2))
    assert repeated_standalone_positions(raw, page_type="scheme") == expected
    reader = PyMuPdfReader(render_max_side=300, text_limit=1200)
    extracted = reader.extract(document.tobytes(), selected_pages=(1,)).pages[0]
    assert repeated_standalone_positions(extracted.text, page_type="scheme") == expected
    assert "   " not in extracted.text
    assert "Reference to 8.9.4 in prose." in extracted.text
    assert extracted.text_words == reader._extract_text_words(page)
    assert extracted.rendered_png == reader._render_page(page)
    text_only = reader.extract_text(document.tobytes(), selected_pages=(1,))[0].text
    assert text_only == extracted.text
