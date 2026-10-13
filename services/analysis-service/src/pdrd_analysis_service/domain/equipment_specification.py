# services/analysis-service/src/pdrd_analysis_service/domain/equipment_specification.py

"""Связывает структурированную спецификацию с существующим реестром EQ без догадок."""

import math
import re
from copy import deepcopy
from typing import Any


class SpecificationContractError(ValueError):
    """Нарушение полноты или геометрии переданной спецификации."""


class SpecificationCancelled(RuntimeError):
    """Отмена между короткими уточнениями и основным пониманием листа."""


def validated_specification(value: dict | None, page_number: int) -> dict | None:
    """Проверяет исходные строки и идентичность позиции перед исключением её из VLM-ответа."""
    if value is None:
        return None
    specification = deepcopy(value)
    if specification.get("version") != 1 or specification.get("page") != page_number:
        raise SpecificationContractError(
            "Версия или страница спецификации не совпадает с листом."
        )
    rows, items = specification.get("raw_rows"), specification.get("items")
    if (
        not isinstance(rows, list)
        or not isinstance(items, list)
        or specification.get("raw_row_count") != len(rows)
    ):
        raise SpecificationContractError(
            "Контроль количества исходных строк спецификации не пройден."
        )
    ids = []
    for row in rows:
        if (
            not isinstance(row, dict)
            or row.get("page") != page_number
            or not isinstance(row.get("raw_cells"), list)
        ):
            raise SpecificationContractError(
                "Исходная строка не связана с физической страницей."
            )
        ids.append(row.get("row_id"))
        _valid_region(row.get("bbox"))
    if any(
        not isinstance(identifier, str) or not identifier for identifier in ids
    ) or len(ids) != len(set(ids)):
        raise SpecificationContractError(
            "ID строк спецификации отсутствуют или повторяются."
        )
    item_ids = []
    for item in items:
        if not isinstance(item, dict):
            raise SpecificationContractError(
                "Позиция спецификации должна быть объектом."
            )
        item_ids.append(item.get("item_id"))
        if any(
            not isinstance(item.get(field), str)
            for field in (
                "position",
                "name",
                "model",
                "manufacturer",
                "variant",
                "units",
                "quantity",
                "project_code",
                "under",
                "Caption",
                "Units",
                "Quantity",
                "UserComment1",
                "Under",
            )
        ):
            raise SpecificationContractError("Поля позиции должны быть строками.")
        if not item.get("row_ids") or len(item["row_ids"]) != len(
            item.get("raw_values", [])
        ):
            raise SpecificationContractError(
                "Позиция не сохраняет все исходные ячейки."
            )
        if any(
            not isinstance(identifier, str) for identifier in item["row_ids"]
        ) or len(item["row_ids"]) != len(set(item["row_ids"])):
            raise SpecificationContractError("В позиции повторены исходные строки.")
        if not item.get("source_pages") or not item.get("source_regions"):
            raise SpecificationContractError(
                "Позиция не связана с исходными страницами и областями."
            )
        for region in item["source_regions"]:
            if (
                not isinstance(region, dict)
                or region.get("page") not in item["source_pages"]
                or region.get("row_id") not in item["row_ids"]
            ):
                raise SpecificationContractError(
                    "Область позиции относится к другой странице."
                )
            _valid_region(region)
    if any(not identifier for identifier in item_ids) or len(item_ids) != len(
        set(item_ids)
    ):
        raise SpecificationContractError("ID позиций спецификации повторяются.")
    expected = sorted(
        row["row_id"]
        for row in rows
        if row.get("kind")
        not in {
            "header",
            "column_numbers",
            "blank",
            "section",
            "footer",
            "cross_page_continuation",
        }
    )
    actual = sorted(
        row_id for item in items for row_id in item["row_ids"] if row_id in ids
    )
    if expected != actual:
        raise SpecificationContractError(
            "Потеря или дублирование исходных строк спецификации."
        )
    return specification


def _valid_region(region: dict | None) -> None:
    """Не допускает нечисловых, перевёрнутых и выходящих за страницу областей."""
    if not isinstance(region, dict):
        raise SpecificationContractError("Область строки спецификации отсутствует.")
    coordinates = [region.get(key) for key in ("x_min", "y_min", "x_max", "y_max")]
    if any(
        isinstance(v, bool)
        or not isinstance(v, (int, float))
        or not math.isfinite(v)
        or not 0 <= v <= 1000
        for v in coordinates
    ):
        raise SpecificationContractError("Область строки спецификации недопустима.")
    if coordinates[0] >= coordinates[2] or coordinates[1] >= coordinates[3]:
        raise SpecificationContractError(
            "Область строки спецификации имеет нулевую площадь."
        )


def table_equipment_records(
    specification: dict, page_number: int
) -> tuple[dict[str, Any], ...]:
    """Создаёт EQ-идентичности из отдельных столбцов, сохраняя позиции одной модели отдельно."""
    result = []
    for item in specification["items"]:
        model, manufacturer = item.get("model", ""), item.get("manufacturer", "")
        model_confirmed = (
            bool(model)
            and not item.get("needs_review")
            and not re.match(r"^(?:ГОСТ|СП\s|ТУ\s|опросный|см\.)", model, re.I)
        )
        maker_confirmed = len(manufacturer.strip()) >= 2
        regions = [
            {key: region[key] for key in ("x_min", "y_min", "x_max", "y_max")}
            for region in item["source_regions"]
            if region["page"] == page_number
        ]
        result.append(
            {
                "manufacturer": manufacturer if maker_confirmed else "",
                "model": model,
                "variant": item.get("variant", ""),
                "article": item.get("article", ""),
                "variant_kind": "execution" if item.get("variant") else "none",
                "equipment_type": item.get("name", ""),
                "object_ref": item.get("position") or item["item_id"],
                "evidence_text": item["Caption"],
                "manufacturer_evidence_text": manufacturer,
                "confidence": 1.0 if model_confirmed else 0.0,
                "status": "resolved"
                if maker_confirmed and model_confirmed
                else "needs_review",
                "model_status": "confirmed" if model_confirmed else "needs_review",
                "manufacturer_status": "explicit" if maker_confirmed else "unknown",
                "entity_kind": "equipment",
                "visual_regions": regions,
                "parameters": item.get("parameters", []),
                "extraction_method": "pdf_table",
                "specification_item_id": item["item_id"],
                "source_pages": item["source_pages"],
                "source_regions": item["source_regions"],
                "source_row_ids": item["row_ids"],
                "quantity": item["quantity"],
                "units": item["units"],
                "project_code": item["project_code"],
                "under": item["under"],
            }
        )
    return tuple(result)


def specification_report(pages: list[dict]) -> dict:
    """Возвращает основной пользовательский JSON и исходные строки отдельно от уникальных моделей."""
    specifications = [
        page.get("facts", {}).get("equipment_specification") for page in pages
    ]
    specifications = [spec for spec in specifications if isinstance(spec, dict)]
    items = [item for spec in specifications for item in spec.get("items", [])]
    raw_rows = [row for spec in specifications for row in spec.get("raw_rows", [])]
    expected = sorted(
        row["row_id"]
        for row in raw_rows
        if row.get("kind")
        not in {
            "header",
            "column_numbers",
            "blank",
            "section",
            "footer",
        }
    )
    actual = sorted(row_id for item in items for row_id in item["row_ids"])
    return {
        "items": [
            {
                key: item.get(key, "")
                for key in ("Caption", "Units", "Quantity", "UserComment1", "Under")
            }
            for item in items
        ],
        "structured_items": items,
        "raw_rows": raw_rows,
        "raw_row_count": len(raw_rows),
        "item_count": len(items),
        "pages": [spec["page"] for spec in specifications],
        "coverage_complete": bool(specifications)
        and expected == actual
        and all(
            spec.get("coverage_complete", False) and spec.get("status") == "reliable"
            for spec in specifications
        ),
        "warnings": [
            warning for spec in specifications for warning in spec.get("warnings", [])
        ],
    }
