# services/document-service/tests/integration/test_specification_pdf.py

"""Реальный PDF ИОС: все исходные ячейки, физические позиции и короткие переносы."""

import hashlib
import re
from pathlib import Path

import fitz
import pytest
from pdrd_document_service.infrastructure.pdf.pymupdf import PyMuPdfReader

PDF = Path(__file__).parents[4] / "tests/fixtures/equipment/ios-4-1-7.pdf"


@pytest.fixture(scope="module")
def document():
    """Читает восемь реальных листов одним production reader без внешних сервисов."""
    content = PDF.read_bytes()
    assert (
        hashlib.sha256(content).hexdigest()
        == "9ccabd0f34f2950ad0c2dd8752b351ffe54ab6b8455ddc4b7887f402733022f0"
    )
    return PyMuPdfReader(render_max_side=700, text_limit=12000).extract(
        content,
        selected_pages=tuple(range(24, 32)),
        include_equipment_specification=True,
    )


def test_every_original_table_cell_and_position_survives(document):
    """Сравнивает с независимым извлечением PyMuPDF, включая None, footer и заголовки."""
    expected_positions = []
    with fitz.open(PDF) as original:
        for page in document.pages:
            source = original[page.number - 1].find_tables().tables[0].extract()
            specification = page.equipment_specification
            assert (
                specification["status"] == "reliable"
                and specification["coverage_complete"]
            )
            assert len(source) == specification["raw_row_count"]
            assert [
                [cell["text"] for cell in row["raw_cells"]]
                for row in specification["raw_rows"]
            ] == source
            expected_positions.extend(
                re.sub(r"\s+", "", row[0])
                for index, row in enumerate(source)
                if index > 1 and row[0] and re.fullmatch(r"[\d.\s\-–—]+", row[0])
            )
    specifications = [page.equipment_specification for page in document.pages]
    actual_positions = [
        item["position"]
        for spec in specifications
        for item in spec["items"]
        if item["position"]
    ]
    assert actual_positions == expected_positions
    assert len(actual_positions) == 129
    assert sum(spec["raw_row_count"] for spec in specifications) == 255
    assert sum(len(spec["items"]) for spec in specifications) == 131


def test_page_25_example_has_exact_model_manufacturer_quantity_and_full_name(document):
    """Контрольная позиция не смешивает поля и восстанавливает перенос слова «редуктора»."""
    item = document.pages[1].equipment_specification["items"][0]
    assert item["position"] == "3.8.1-3.8.6"
    assert item["model"] == "КШ.Ц.Ф.Р200/150.016.Н/П.02"
    assert item["manufacturer"] == "ЧСГС"
    assert item["name"].endswith("под установку редуктора")
    assert (item["Units"], item["Quantity"], item["UserComment1"], item["Under"]) == (
        "шт.",
        "6",
        "120/155-16-ИОС4.1.7",
        "Котловой контур",
    )
    assert item["source_pages"] == [25]
    assert len(item["source_regions"]) == 2
    for region in item["source_regions"]:
        assert 0 <= region["x_min"] < region["x_max"] <= 1000
        assert 0 <= region["y_min"] < region["y_max"] <= 1000


def test_unknown_positions_are_kept_and_section_is_only_explicit(document):
    """Гильзы без позиции не удаляются и не объединяются с датчиком по догадке."""
    all_items = [
        item
        for page in document.pages
        for item in page.equipment_specification["items"]
    ]
    unknown = [item for item in all_items if not item["position"]]
    assert [item["name"] for item in unknown] == ["Гильза L200мм.", "Гильза L100мм"]
    assert all(item["needs_review"] and item["Quantity"] == "2" for item in unknown)
    assert any(item["Under"] == "Водоснабжение" for item in all_items)
    assert any(item["Under"] == "Топливоснабжение" for item in all_items)


def test_disabled_feature_does_not_call_table_finder(monkeypatch):
    """Обычное чтение PDF сохраняет прежний маршрут и не запускает таблицы."""

    def forbidden(*args, **kwargs):
        """Немедленно показывает нежелательное расширение обычного чтения."""
        raise AssertionError("find_tables вызван без EQ")

    monkeypatch.setattr(fitz.Page, "find_tables", forbidden)
    page = (
        PyMuPdfReader(render_max_side=500, text_limit=1000)
        .extract(PDF.read_bytes(), selected_pages=(25,))
        .pages[0]
    )
    assert page.equipment_specification is None


def test_scanned_specification_stays_on_original_visual_path():
    """Изображение той же страницы не выдаёт выдуманную надёжную текстовую таблицу."""
    with fitz.open(PDF) as source:
        image = source[24].get_pixmap(matrix=fitz.Matrix(1, 1)).tobytes("png")
    with fitz.open() as scan:
        page = scan.new_page(width=900, height=700)
        page.insert_image(page.rect, stream=image)
        content = scan.tobytes()
    result = PyMuPdfReader(render_max_side=500, text_limit=12000).extract(
        content, selected_pages=(1,), include_equipment_specification=True
    )
    assert result.pages[0].equipment_specification is None
    assert result.pages[0].text == ""
    assert result.pages[0].rendered_png


def test_table_finder_failure_returns_explicit_fallback_without_breaking_pdf(
    monkeypatch,
):
    """Ошибка табличного анализа не лишает страницу текста и изображения для прежнего VLM."""

    def unavailable(*args, **kwargs):
        """Имитирует недоступность распознавания геометрии одной таблицы."""
        raise RuntimeError("table detection failure")

    monkeypatch.setattr(fitz.Page, "find_tables", unavailable)
    result = PyMuPdfReader(render_max_side=500, text_limit=12000).extract(
        PDF.read_bytes(), selected_pages=(25,), include_equipment_specification=True
    )
    page = result.pages[0]
    assert page.equipment_specification["status"] == "unavailable"
    assert page.equipment_specification["warnings"]
    assert page.text and page.rendered_png
