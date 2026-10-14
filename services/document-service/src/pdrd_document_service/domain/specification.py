# services/document-service/src/pdrd_document_service/domain/specification.py

"""Нормализация спецификации с сохранением каждой исходной строки и её геометрии."""

import re
from copy import deepcopy

from pdrd_document_service.domain.specification_parameters import (
    specification_parameters,
)

_HEADERS = {
    "position": ("позиция", "поз.", "поз"),
    "name": ("наименование", "характеристика"),
    "model": ("тип", "марка", "обозначение документа"),
    "article": ("код оборудования", "код изделия", "артикул"),
    "manufacturer": ("заводизготовитель", "изготовитель", "производитель"),
    "units": ("единица", "ед. изм", "едизм"),
    "quantity": ("количество", "колич", "колво"),
    "mass": ("масса",),
    "note": ("примечание",),
}
_STRUCTURAL = {"header", "column_numbers", "blank", "section", "footer"}


def clean_cell(value: str | None, *, identifier: bool = False) -> str:
    """Убирает переносы оформления, сохраняя исходное значение отдельно в raw_cells."""
    value = value or ""
    if identifier:
        value = re.sub(r"(?<=[\w/.-])\n(?=[\w/.-])", "", value)
    return re.sub(r"\s+", " ", value).strip()


def project_code_from_text(text: str) -> str:
    """Читает явный шифр; неоднозначные обозначения сохраняет пустыми."""
    explicit = re.findall(
        r"(?:шифр\s+(?:проекта|документа)|код\s+проекта)\s*[:=]\s*([\w/.-]+)",
        text,
        re.I,
    )
    candidates = explicit or re.findall(
        r"[\w/]+(?:-[\w/]+)*-ИОС\s*\d+(?:\.\d+)*", text, re.I
    )
    candidates = list(dict.fromkeys(candidates))
    return candidates[0] if len(candidates) == 1 else ""


def specification_columns(values: list[str | None]) -> dict[str, list[int]]:
    """Разворачивает объединённую ячейку заголовка на все её подстолбцы."""
    starts = []
    for index, value in enumerate(values):
        if not value:
            continue
        token = re.sub(r"[\s.\-–—]+", "", value.casefold())
        for role, names in _HEADERS.items():
            if any(re.sub(r"[\s.]+", "", name) in token for name in names):
                starts.append((index, role))
                break
    roles = {}
    for number, (start, role) in enumerate(starts):
        end = starts[number + 1][0] if number + 1 < len(starts) else len(values)
        roles[role] = list(range(start, end))
    return (
        roles
        if {"position", "name", "model", "units", "quantity"} <= roles.keys()
        else {}
    )


def _values(row: dict, columns: dict[str, list[int]]) -> dict[str, str]:
    """Читает только ячейки соответствующего столбца, не переставляя значения."""
    return {
        role: clean_cell(
            " ".join(str(row["raw_cells"][i]["text"] or "") for i in indices),
            identifier=role in {"position", "model", "article"},
        )
        for role, indices in columns.items()
    }


def _region(row: dict) -> dict:
    """Связывает область строки с её физической страницей и идентификатором."""
    return {"page": row["page"], "row_id": row["row_id"], **row["bbox"]}


def _new_item(row: dict, values: dict, project_code: str, under: str) -> dict:
    """Создаёт структурированную позицию и пользовательский Caption без догадок."""
    item = {
        field: values.get(field, "")
        for field in (
            "position",
            "name",
            "model",
            "manufacturer",
            "article",
            "units",
            "quantity",
            "mass",
            "note",
        )
    }
    variant_match = re.fullmatch(
        r"(.+?)\s+(исп(?:олнение)?\.?\s+.+)", item["model"], re.I
    )
    manufacturer = item["manufacturer"]
    if len(manufacturer) >= 2 and manufacturer[0] in '"«' and manufacturer[-1] in '"»':
        item["manufacturer"] = manufacturer[1:-1].strip()
    item.update(
        variant=variant_match.group(2) if variant_match else "",
        parameters=[],
        project_code=project_code,
        under=under,
        item_id=row["row_id"],
        row_ids=[row["row_id"]],
        source_pages=[row["page"]],
        source_regions=[_region(row)],
        raw_values=[deepcopy(row["raw_cells"])],
        needs_review=not bool(item["position"] and item["name"]),
    )
    if variant_match:
        item["model"] = variant_match.group(1)
    _caption(item)
    return item


def _caption(item: dict) -> None:
    """Синхронизирует основной пользовательский формат со структурированными полями."""
    item.update(
        Caption="; ".join(
            value
            for value in (
                item["name"],
                " ".join(value for value in (item["model"], item["variant"]) if value),
                item["manufacturer"],
            )
            if value
        ),
        Units=item["units"],
        Quantity=item["quantity"],
        UserComment1=item["project_code"],
        Under=item["under"],
    )
    item["parameters"] = specification_parameters(item["name"])


def _append_item(item: dict, row: dict, values: dict) -> None:
    """Дополняет доказанное продолжение, сохраняя исходные ячейки и все страницы."""
    for field in ("name", "model", "manufacturer", "article", "note"):
        if values.get(field) and values[field] != item[field]:
            item[field] = clean_cell(
                item[field] + " " + values[field],
                identifier=field in {"model", "article"},
            )
    for field in ("units", "quantity", "mass"):
        if values.get(field) and not item[field]:
            item[field] = values[field]
    item["row_ids"].append(row["row_id"])
    item["source_pages"] = sorted({*item["source_pages"], row["page"]})
    item["source_regions"].append(_region(row))
    item["raw_values"].append(deepcopy(row["raw_cells"]))
    _caption(item)


def normalize_specification(
    tables: list[dict], *, page: int, project_code: str
) -> dict:
    """Разбирает все строки распознанных таблиц и проверяет полноту их представления."""
    rows, items, warnings = [], [], []
    under = ""
    for table in tables:
        columns = table["columns"]
        current = None
        anchor_bottom = None
        for row in table["rows"]:
            row = deepcopy(row)
            rows.append(row)
            values = _values(row, columns)
            nonempty = {role for role, value in values.items() if value}
            if row["row_index"] <= table["header_index"]:
                row["kind"] = "header"
            elif not nonempty:
                row["kind"] = "blank"
            elif (
                all(value.isdigit() for value in values.values() if value)
                and values.get("position") == "1"
                and values.get("name") == "2"
            ):
                row["kind"] = "column_numbers"
            elif any(
                marker in values.get("position", "").casefold()
                for marker in ("из м.", "изм.", "лист", "подп.")
            ) or (
                not values.get("name")
                and not values.get("model")
                and not values.get("units")
                and not values.get("quantity")
            ):
                row["kind"] = "footer"
            else:
                position_cell = row["raw_cells"][columns["position"][0]]
                continuation = (
                    current is not None
                    and position_cell["text"] is None
                    and not values.get("position")
                    and anchor_bottom is not None
                    and row["bbox"]["y_min"] < anchor_bottom
                    and not any(
                        values.get(field)
                        for field in (
                            "units",
                            "quantity",
                            "model",
                            "manufacturer",
                            "article",
                        )
                    )
                )
                if continuation:
                    row["kind"] = "continuation"
                    row["item_id"] = current["item_id"]
                    _append_item(current, row, values)
                elif (
                    nonempty == {"name"}
                    and position_cell["text"] == ""
                    and re.search(
                        r"контур|система|оборудование|материалы|арматура|газоснабжение|водоснабжение|топливоснабжение|трубопровод",
                        values["name"],
                        re.I,
                    )
                ):
                    row["kind"] = "section"
                    under = values["name"]
                    current = None
                else:
                    row["kind"] = "item" if values.get("position") else "ambiguous"
                    current = _new_item(row, values, project_code, under)
                    row["item_id"] = current["item_id"]
                    items.append(current)
                    if row["kind"] == "ambiguous":
                        current["needs_review"] = True
                        warnings.append(
                            f"Строка {row['row_id']}: требуется уточнение связи с позицией."
                        )
                    anchor_bottom = (position_cell.get("bbox") or {}).get("y_max")
    return {
        "version": 1,
        "page": page,
        "status": "reliable" if rows else "unavailable",
        "project_code": project_code,
        "items": items,
        "raw_rows": rows,
        "raw_row_count": len(rows),
        "warnings": warnings,
        "coverage_complete": coverage_complete(rows, items),
    }


def coverage_complete(rows: list[dict], items: list[dict]) -> bool:
    """Каждая неслужебная строка должна входить ровно в одну сохранённую позицию."""
    expected = [row["row_id"] for row in rows if row["kind"] not in _STRUCTURAL]
    actual = [row_id for item in items for row_id in item["row_ids"]]
    return len(expected) == len(set(expected)) and sorted(expected) == sorted(actual)


def join_specification_pages(specifications: list[dict | None]) -> None:
    """Соединяет только явные межстраничные продолжения, оставляя неоднозначные строки."""
    previous = None
    last_item = None
    inherited_under = ""
    for specification in specifications:
        if specification is None:
            previous = None
            last_item = None
            inherited_under = ""
            continue
        connected = bool(
            previous
            and specification["page"] == previous["page"] + 1
            and specification["project_code"]
            and specification["project_code"] == previous["project_code"]
        )
        if not connected:
            last_item = None
            inherited_under = ""
        if connected:
            for item in specification["items"]:
                if item["under"]:
                    inherited_under = item["under"]
                elif inherited_under:
                    item["under"] = inherited_under
                    _caption(item)
            if last_item is not None and specification["items"]:
                last, first = last_item, specification["items"][0]
                # Одинаковый номер и явная помета; конфликт количеств/моделей запрещает объединение.
                marker = re.match(
                    r"(?:продолжение|продолж\.)\s*(?:позиции|поз\.)?\s*(\d[\d.-]*)?\s*[:;]?\s*",
                    first["name"],
                    re.I,
                )
                same = first["position"] == last["position"] and bool(first["position"])
                conflicts = any(
                    first[field] and last[field] and first[field] != last[field]
                    for field in ("model", "manufacturer", "units", "quantity")
                )
                if (
                    marker
                    and same
                    and not conflicts
                    and (not marker.group(1) or marker.group(1) == last["position"])
                ):
                    first["name"] = first["name"][marker.end() :].strip()
                    for row in specification["raw_rows"]:
                        if row.get("item_id") == first["item_id"]:
                            initial = row["row_id"] == first["row_ids"][0]
                            values = {
                                field: first[field] if initial else ""
                                for field in (
                                    "name",
                                    "model",
                                    "manufacturer",
                                    "article",
                                    "note",
                                    "units",
                                    "quantity",
                                    "mass",
                                )
                            }
                            _append_item(last, row, values)
                            row["kind"] = "cross_page_continuation"
                            row["item_id"] = last["item_id"]
                    specification["items"].pop(0)
        if specification["items"]:
            last_item = specification["items"][-1]
            inherited_under = specification["items"][-1]["under"]
        previous = specification
    all_items = [item for spec in specifications if spec for item in spec["items"]]
    all_rows = [row for spec in specifications if spec for row in spec["raw_rows"]]
    complete = coverage_complete(all_rows, all_items)
    for spec in specifications:
        if spec is not None:
            spec["coverage_complete"] = complete
