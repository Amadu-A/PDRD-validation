# services/document-service/src/pdrd_document_service/infrastructure/pdf/specification.py

"""Извлечение исходных ячеек и областей спецификации средствами PyMuPDF."""

import math
import re

import fitz

from pdrd_document_service.domain.specification import (
    normalize_specification,
    project_code_from_text,
    specification_columns,
)
from pdrd_document_service.infrastructure.pdf.page_content import page_content_metadata


def _box(rect: tuple | fitz.Rect, page: fitz.Page) -> dict[str, int]:
    """Переводит область PDF в общую систему 0..1000 с учётом поворота."""
    rect = fitz.Rect(rect)
    if page.rotation:
        rect = rect * page.rotation_matrix
    return {
        "x_min": max(0, math.floor(rect.x0 / page.rect.width * 1000)),
        "y_min": max(0, math.floor(rect.y0 / page.rect.height * 1000)),
        "x_max": min(1000, math.ceil(rect.x1 / page.rect.width * 1000)),
        "y_max": min(1000, math.ceil(rect.y1 / page.rect.height * 1000)),
    }


def extract_specification(page: fitz.Page, page_number: int, text: str) -> dict | None:
    """Ищет структуру спецификации до рендера; неизвестные таблицы не признаёт надёжными."""
    normalized = re.sub(r"[\s\-–—]+", "", text.casefold())
    if not (
        "наименование" in normalized
        and ("позиция" in normalized or "поз." in normalized)
        and ("марка" in normalized or "изготовитель" in normalized)
    ):
        return None
    project_code = project_code_from_text(text)
    result = []
    row_boxes = {}
    try:
        tables = page.find_tables().tables
        for table_number, table in enumerate(tables, 1):
            values = table.extract()
            header_index, columns = next(
                (
                    (index, columns)
                    for index, row in enumerate(values)
                    if (columns := specification_columns(row))
                ),
                (None, {}),
            )
            if header_index is None:
                continue
            rows = []
            for index, (values_row, geometry) in enumerate(
                zip(values, table.rows, strict=True)
            ):
                cells = [
                    {"text": value, "bbox": _box(cell, page) if cell else None}
                    for value, cell in zip(values_row, geometry.cells, strict=True)
                ]
                rows.append(
                    {
                        "row_id": f"spec-p{page_number:04d}-t{table_number:02d}-r{index:04d}",
                        "page": page_number,
                        "table_id": f"p{page_number}-t{table_number}",
                        "row_index": index,
                        "bbox": _box(geometry.bbox, page),
                        "raw_cells": cells,
                    }
                )
            result.append(
                {"header_index": header_index, "columns": columns, "rows": rows}
            )
            row_boxes.update(
                {
                    row["row_id"]: geometry.bbox
                    for row, geometry in zip(rows, table.rows, strict=True)
                }
            )
    except Exception:
        if result:
            partial = normalize_specification(
                result, page=page_number, project_code=project_code
            )
            partial["status"] = "partial"
            partial["warnings"].append(
                "Часть таблиц не распознана; сохранённые строки требуют дополнения."
            )
            return partial
        return {
            "version": 1,
            "page": page_number,
            "status": "unavailable",
            "items": [],
            "raw_rows": [],
            "raw_row_count": 0,
            "coverage_complete": False,
            "project_code": project_code,
            "warnings": [
                "Таблица не распознана надёжно; требуется визуальное извлечение."
            ],
        }
    if not result:
        return None
    specification = normalize_specification(
        result, page=page_number, project_code=project_code
    )
    boxes_by_table = {}
    for row in specification["raw_rows"]:
        if row["kind"] in {"blank", "footer"}:
            continue
        box = fitz.Rect(row_boxes[row["row_id"]])
        if row["table_id"] in boxes_by_table:
            boxes_by_table[row["table_id"]] |= box
        else:
            boxes_by_table[row["table_id"]] = box
    try:
        specification["page_content"] = page_content_metadata(
            page, list(boxes_by_table.values()), tables
        )
    except Exception:
        specification["page_content"] = {
            "version": 1,
            "route": "mixed",
            "requires_vision": True,
            "reason": "page_content_inspection_failed",
        }
    return specification
