# services/document-service/tests/unit/test_specification.py

"""Исходные строки, объединённые ячейки и межстраничные продолжения спецификации."""

from copy import deepcopy

import pytest
from pdrd_document_service.domain.specification import (
    coverage_complete,
    join_specification_pages,
    normalize_specification,
    project_code_from_text,
    specification_columns,
)
from pdrd_document_service.domain.specification_parameters import (
    specification_parameters,
)

HEADERS = [
    "Позиция",
    "Наименование и техническая характеристика",
    "Тип, марка",
    "Код оборудования",
    "Завод-изготовитель",
    "Единица измерения",
    "Коли-\nчество",
    "Масса",
    "Примечание",
]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Шифр проекта: 42-2026-ОВ1", "42-2026-ОВ1"),
        ("Код проекта = ABC-25-ЭОМ", "ABC-25-ЭОМ"),
        ("Шифр документа: 42-ОВ1\nШифр документа: 42-ОВ1", "42-ОВ1"),
        ("Шифр проекта: 42-ОВ1\nШифр проекта: 43-ОВ1", ""),
        ("Модель оборудования MODEL-10", ""),
    ],
)
def test_project_code_requires_explicit_label_or_unambiguous_ios_stamp(text, expected):
    """Шифр другого раздела читается по подписи; модель изделия не становится шифром."""
    assert project_code_from_text(text) == expected


def row(index, values, *, page=1, span_bottom=None):
    """Создаёт независимую геометрию ячеек, включая явный rowspan позиции."""
    top = index * 20
    cells = []
    for column, value in enumerate(values):
        bbox = {
            "x_min": column * 100,
            "x_max": (column + 1) * 100,
            "y_min": top,
            "y_max": span_bottom if column == 0 and span_bottom else top + 20,
        }
        cells.append({"text": value, "bbox": bbox if value is not None else None})
    return {
        "row_id": f"spec-p{page:04d}-t01-r{index:04d}",
        "page": page,
        "table_id": f"p{page}-t1",
        "row_index": index,
        "bbox": {"x_min": 0, "x_max": 900, "y_min": top, "y_max": top + 20},
        "raw_cells": cells,
    }


def specification(values, *, page=1, span_bottom=None):
    """Подготавливает небольшую таблицу с исходными заголовками без PyMuPDF."""
    rows = [row(0, HEADERS, page=page)]
    rows += [
        row(index, item, page=page, span_bottom=span_bottom if index == 1 else None)
        for index, item in enumerate(values, 1)
    ]
    return normalize_specification(
        [{"rows": rows, "columns": specification_columns(HEADERS), "header_index": 0}],
        page=page,
        project_code="120/155-16-ИОС4.1.7",
    )


def test_header_spans_map_fragmented_name_cells_to_the_same_column():
    """None в объединённом заголовке не смещает производителя, единицу и количество."""
    headers = [
        "Позиция",
        "Наименование",
        None,
        None,
        "Тип, марка",
        "Единица",
        "Количество",
    ]
    columns = specification_columns(headers)
    assert columns["name"] == [1, 2, 3]
    assert columns["model"] == [4]
    assert columns["quantity"] == [6]


def test_abbreviated_units_header_is_recognized_without_shifting_columns():
    """Краткая подпись «Ед. изм.» допускается вместе с «Поз.» и «Кол-во»."""
    columns = specification_columns(
        ["Поз.", "Наименование", "Тип, марка", "Ед. изм.", "Кол-во"]
    )
    assert columns["units"] == [3] and columns["quantity"] == [4]


def test_multiline_item_preserves_every_original_cell_and_page_region():
    """Продолжение внутри rowspan объединяется, а исходная строка остаётся доступной."""
    values = [
        [
            "3.8.1-\n3.8.6",
            "Кран под установку",
            "КШ.Ц.Ф.Р200",
            "",
            '"ЧСГС"',
            "шт.",
            "6",
            "85",
            "",
        ],
        [None, "редуктора", None, None, None, None, None, None, None],
    ]
    result = specification(values, page=25, span_bottom=65)
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["position"] == "3.8.1-3.8.6"
    assert item["name"] == "Кран под установку редуктора"
    assert item["manufacturer"] == "ЧСГС"
    assert (item["Quantity"], item["Units"]) == ("6", "шт.")
    assert item["source_pages"] == [25]
    assert (
        len(item["row_ids"])
        == len(item["raw_values"])
        == len(item["source_regions"])
        == 2
    )
    assert item["raw_values"][0][0]["text"] == values[0][0]
    assert result["coverage_complete"]


def test_missing_position_is_preserved_and_another_product_is_not_merged():
    """Вторая комплектующая со своим количеством остаётся отдельной даже в rowspan."""
    result = specification(
        [
            ["1", "Датчик", "DT-10", "", "Завод", "шт.", "2", "", ""],
            [None, "Гильза", None, None, "Другой завод", "шт.", "2", "", ""],
        ],
        span_bottom=65,
    )
    assert len(result["items"]) == 2
    assert result["items"][1]["needs_review"]
    assert result["items"][1]["position"] == ""
    assert result["coverage_complete"]


@pytest.mark.parametrize("changed", ["position", "model", "manufacturer", "quantity"])
def test_cross_page_continuation_requires_same_position_and_no_conflicts(changed):
    """Помета продолжения не разрешает объединение другой позиции или модели."""
    first = specification([["1", "Кран", "КШ-10", "", "Завод", "шт.", "1", "", ""]])
    second = specification(
        [["1", "Продолжение позиции 1: с приводом", "", "", "", "", "", "", ""]], page=2
    )
    second["items"][0][changed] = "другое"
    join_specification_pages([first, second])
    assert len(first["items"]) == len(second["items"]) == 1


def test_cross_page_continuation_preserves_rows_and_does_not_duplicate_model():
    """Доказанное продолжение хранит две страницы, старые ячейки и одну позицию."""
    first = specification([["1", "Кран", "КШ-10", "", "Завод", "шт.", "1", "", ""]])
    second = specification(
        [
            [
                "1",
                "Продолжение позиции 1: с приводом",
                "КШ-10",
                "",
                "Завод",
                "",
                "",
                "",
                "",
            ]
        ],
        page=2,
    )
    before = deepcopy([first["raw_rows"], second["raw_rows"]])
    join_specification_pages([first, second])
    item = first["items"][0]
    assert item["model"] == "КШ-10"
    assert item["name"] == "Кран с приводом"
    assert item["source_pages"] == [1, 2]
    assert len(second["items"]) == 0
    for original, current in zip(
        before, [first["raw_rows"], second["raw_rows"]], strict=True
    ):
        assert [row["raw_cells"] for row in original] == [
            row["raw_cells"] for row in current
        ]
    assert coverage_complete(first["raw_rows"] + second["raw_rows"], first["items"])


def test_different_page_or_project_does_not_inherit_group_or_merge():
    """Одинаковая подпись модели не соединяет разные спецификации."""
    first = specification(
        [
            ["", "Котловой контур", "", "", "", "", "", "", ""],
            ["1", "Кран", "КШ-10", "", "Завод", "шт.", "1", "", ""],
        ]
    )
    second = specification(
        [["1", "Продолжение позиции 1: с приводом", "", "", "", "", "", "", ""]], page=3
    )
    join_specification_pages([first, second])
    assert second["items"][0]["Under"] == ""
    assert len(first["items"]) == 1


def test_execution_is_separate_and_caption_preserves_it():
    """Исполнение остаётся в Caption и отдельно от базовой модели."""
    item = specification(
        [["1", "Блок питания", "РИП-12 исп. 51", "", "Болид", "шт.", "1", "", ""]]
    )["items"][0]
    assert (item["model"], item["variant"]) == ("РИП-12", "исп. 51")
    assert "РИП-12 исп. 51" in item["Caption"]


def test_three_page_continuation_retains_every_source_row():
    """Промежуточная страница без новой позиции не разрывает доказанное продолжение."""
    first = specification([["1", "Кран", "КШ-10", "", "Завод", "шт.", "1", "", ""]])
    second = specification(
        [["1", "Продолжение позиции 1: с приводом", "", "", "", "", "", "", ""]], page=2
    )
    third = specification(
        [["1", "Продолжение позиции 1: и кабелем", "", "", "", "", "", "", ""]], page=3
    )
    join_specification_pages([first, second, third])
    assert not second["items"] and not third["items"]
    assert first["items"][0]["source_pages"] == [1, 2, 3]
    assert first["items"][0]["name"] == "Кран с приводом и кабелем"
    assert all(spec["coverage_complete"] for spec in (first, second, third))


def test_parameters_keep_heat_and_electric_power_distinct_and_do_not_invent_units():
    """Характеристики читаются из наименования, не из модели или Caption."""
    parameters = specification_parameters(
        "Теплопроизводительность - 4500 кВт; потребляемая мощность 12 Вт; IP54; Ду200; Ру16; РИП-12"
    )
    assert {row["property_name"] for row in parameters} == {
        "thermal_power",
        "power",
        "protection_degree",
    }
    assert (
        next(row for row in parameters if row["property_name"] == "thermal_power")[
            "value_raw"
        ]
        == "4500"
    )
    assert not specification_parameters("РИП-12, Ду200, Ру16")
