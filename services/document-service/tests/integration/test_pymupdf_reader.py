# services/document-service/tests/integration/test_pymupdf_reader.py

"""Integration-тест PyMuPDF adapter."""

import fitz
import pytest
from pdrd_document_service.domain.pdf import (
    PdfPageType,
)
from pdrd_document_service.infrastructure.pdf.pymupdf import (
    PyMuPdfReader,
)


def build_pdf() -> bytes:
    """Создаёт небольшой PDF полностью в памяти."""
    document = fitz.open()

    first_page = document.new_page()

    first_page.insert_text(
        (
            72,
            72,
        ),
        "Project documentation",
    )

    second_page = document.new_page()

    second_page.insert_text(
        (
            72,
            72,
        ),
        ("Test drawing page XT1 SG K2-1PT201"),
    )

    content = document.tobytes()

    document.close()

    return content


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_vector_lines_share_word_coordinate_system_and_keep_path_provenance(rotation):
    """Векторные пути используют тот же поворот и масштаб, что и PDF-слова."""
    document = fitz.open()
    page = document.new_page(width=400, height=200)
    page.insert_text((40, 60), "S1 L=15m")
    page.draw_line((40, 60), (120, 60), color=(0, 0, 1), width=0.5)
    page.draw_line((120, 60), (120, 140), color=(0, 0, 1), width=2)
    page.set_rotation(rotation)
    content = document.tobytes()
    document.close()
    result = (
        PyMuPdfReader(render_max_side=800, text_limit=2000)
        .extract(content, selected_pages=(1,))
        .pages[0]
    )
    assert len(result.graphic_lines) == 2 and result.text_words
    assert all(line["id"] for line in result.graphic_lines)
    assert result.graphic_lines[0]["color"] == [0, 0, 1]
    with fitz.open(stream=content, filetype="pdf") as reopened:
        expected = fitz.Point(40, 60) * reopened[0].rotation_matrix
        line = result.graphic_lines[0]
        assert line["x1"] == pytest.approx(
            expected.x / reopened[0].rect.width * 1000, abs=0.001
        )
        assert line["y1"] == pytest.approx(
            expected.y / reopened[0].rect.height * 1000, abs=0.001
        )


def test_reader_extracts_selected_page_png_and_text_geometry() -> None:
    """Проверяет text, PNG и реальные PDF word bbox."""
    reader = PyMuPdfReader(
        render_max_side=1200,
        text_limit=12000,
    )

    content = build_pdf()

    assert (
        reader.get_page_count(
            content,
        )
        == 2
    )

    result = reader.extract(
        content,
        selected_pages=(2,),
    )

    assert result.total_pages == 2

    assert result.selected_page_numbers == (2,)

    page = result.pages[0]

    assert page.number == 2

    assert page.page_type is PdfPageType.UNKNOWN

    assert "Test drawing page" in page.text

    assert page.rendered_png.startswith(
        b"\x89PNG\r\n\x1a\n",
    )

    assert page.width_points > 0
    assert page.height_points > 0

    words = {word.text: word for word in page.text_words}

    for anchor in (
        "XT1",
        "SG",
        "K2-1PT201",
    ):
        assert anchor in words

        word = words[anchor]

        assert 0 <= word.bbox.x_min < word.bbox.x_max <= 1000

        assert 0 <= word.bbox.y_min < word.bbox.y_max <= 1000
