# services/analysis-service/src/pdrd_analysis_service/domain/visual_specification.py

"""Полные строки графической спецификации из существующего ответа Understanding.

Чтение изображения не становится программным доказательством идентичности участка.
Номер позиции локален своей странице и группе; исходная строка сохраняется целиком.
"""

import hashlib
import json
import re

from pdrd_analysis_service.domain.document_context import document_fact_from_mapping


def visual_rows_schema():
    """Общий заголовок группы записывается один раз; строки не дублируют имя в цитате."""
    box = {
        "type": "array",
        "minItems": 4,
        "maxItems": 4,
        "items": {"type": "number", "minimum": 0, "maximum": 1000},
    }
    row_fields = {
        key: {"type": "string", "maxLength": 500 if key == "name" else 160}
        for key in (
            "position",
            "name",
            "quantity",
            "unit",
            "reference",
            "manufacturer",
            "model",
            "article",
            "note",
        )
    }
    row_fields["bbox"] = box
    return {
        "type": "array",
        "maxItems": 30,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "system": {
                    "type": "string",
                    "maxLength": 80,
                    "pattern": "^(?:[А-ЯA-Z]{1,3}[0-9]+(?:-[0-9]+)?)?$",
                },
                "header": {"type": "string", "maxLength": 220},
                "bbox": box,
                "rows": {
                    "type": "array",
                    "maxItems": 120,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": row_fields,
                        "required": ["position", "name", "quantity", "unit", "bbox"],
                    },
                },
            },
            "required": ["system", "header", "bbox", "rows"],
        },
    }


def expanded_visual_rows(groups):
    """Разворачивает компактные столбцы без дополнительных гипотез о принадлежности."""
    keys = ("x_min", "y_min", "x_max", "y_max")
    for group in groups if isinstance(groups, list) else ():
        if not isinstance(group, dict) or not isinstance(group.get("rows"), list):
            continue
        if not isinstance(group.get("bbox"), list) or len(group["bbox"]) != 4:
            continue
        for row in group["rows"]:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("bbox"), list)
                or len(row["bbox"]) != 4
            ):
                continue
            if not all(
                isinstance(row.get(key), str)
                for key in ("position", "name", "quantity", "unit")
            ):
                continue
            name = row["name"]
            size = re.search(
                r"[Øø⌀]?(\d+(?:[.,]\d+)?)\s*[xх×*]\s*(\d+(?:[.,]\d+)?)", name
            )
            material = re.search(r"\bПЭ\s*\d+", name, re.I)
            yield {
                "system": group.get("system", ""),
                "system_header": group.get("header", ""),
                "header_bbox": dict(zip(keys, group["bbox"], strict=True)),
                "row_bbox": dict(zip(keys, row["bbox"], strict=True)),
                "position": row["position"],
                "name": name,
                "quantity": row["quantity"],
                "unit": row["unit"],
                "size": size[0] if size else "",
                "material": material[0] if material else "",
                "row_text": " | ".join(
                    str(row.get(key, ""))
                    for key in (
                        "position",
                        "name",
                        "reference",
                        "manufacturer",
                        "model",
                        "article",
                        "unit",
                        "quantity",
                        "note",
                    )
                    if row.get(key)
                ),
                "columns": dict(row),
            }


def visual_specification_facts(rows, page):
    """Сохраняет только полные читаемые строки с отдельной областью заголовка группы."""
    from pdrd_analysis_service.domain.evidence_audit import literal_contains
    from pdrd_analysis_service.domain.physical_identity import valid_box

    result, seen = [], set()
    for row in expanded_visual_rows(rows):
        if not isinstance(row, dict) or not all(
            isinstance(row.get(key), str)
            for key in (
                "system",
                "position",
                "name",
                "material",
                "size",
                "quantity",
                "unit",
                "row_text",
                "system_header",
            )
        ):
            continue
        if not all(
            row[key].strip()
            for key in ("system", "position", "name", "quantity", "unit", "row_text")
        ):
            continue
        if not re.fullmatch(r"[А-ЯA-Z]{1,3}\d+(?:-\d+)?", row["system"]):
            continue
        if not valid_box(row.get("row_bbox")) or not valid_box(row.get("header_bbox")):
            continue
        if not literal_contains(row["system_header"], row["system"]) or not all(
            literal_contains(row["row_text"], row[key])
            for key in ("position", "name", "quantity", "unit")
        ):
            continue
        raw_identity = json.dumps((page, row), ensure_ascii=False, sort_keys=True)
        item_id = (
            "visual-spec-" + hashlib.sha256(raw_identity.encode()).hexdigest()[:20]
        )
        if item_id in seen:
            continue
        seen.add(item_id)
        base = {
            "kind": "parameter",
            "subject_type": "system",
            "identifier": row["system"],
            "subject_name": row["name"],
            "scope_system": row["system"],
            "property_type": "parameter",
            "table_id": f"visual-spec-p{page}",
            "table_title": "Спецификация оборудования, изделий и материалов",
            "row_label": row["name"],
            "source_item_id": item_id,
            "source_row_ids": [f"{item_id}:pos-{row['position']}"],
            "source_pages": [page],
            "source_regions": [
                {
                    "page": page,
                    "role": "specification_row",
                    "text": row["row_text"],
                    "bbox": row["row_bbox"],
                    "row": dict(row),
                },
                {
                    "page": page,
                    "role": "system_header",
                    "text": row["system_header"],
                    "bbox": row["header_bbox"],
                },
            ],
            "visual_regions": [{**row["row_bbox"], "confidence": 0.8}],
            "evidence_text": row["system_header"] + "\n" + row["row_text"],
            "extraction_method": "vlm_specification_row",
            "object_kind": "pipe" if re.search(r"труба\b", row["name"], re.I) else "",
        }
        values = [
            ("Количество поставки", row["quantity"], row["unit"], "supply_quantity")
        ]
        if base["object_kind"] == "pipe" and row["unit"].strip() == "м":
            values = [
                ("Длина поставляемой трубы", row["quantity"], "м", "supply_length")
            ]
        if row["material"] and literal_contains(row["row_text"], row["material"]):
            values.append(("Материал", row["material"], "", "material"))
        size = re.fullmatch(
            r"[Øø⌀]?\s*(\d+(?:[.,]\d+)?)\s*[xх×*]\s*(\d+(?:[.,]\d+)?)", row["size"]
        )
        if size and literal_contains(row["row_text"], row["size"]):
            values.extend(
                [
                    ("Наружный диаметр", size[1], "мм", "outer_diameter"),
                    ("Толщина стенки", size[2], "мм", "wall_thickness"),
                ]
            )
        sdr = re.search(r"\bSDR\s*(\d+(?:[.,]\d+)?)", row["row_text"], re.I)
        if sdr:
            values.append(("SDR", sdr[1], "", "sdr"))
        for prop, value, unit, role in values:
            fact = document_fact_from_mapping(
                {
                    **base,
                    "property_name": prop,
                    "value_raw": value,
                    "unit_raw": unit,
                    "value_role": role,
                }
            )
            if fact is not None:
                result.append(fact)
    return tuple(result)


def visual_equipment_records(facts):
    """Передаёт те же строки в EQ без повторного запроса и без угадывания модели изделия."""
    records, seen = [], set()
    for fact in facts:
        if (
            fact.extraction_method != "vlm_specification_row"
            or fact.source_item_id in seen
        ):
            continue
        source = next(
            region
            for region in fact.source_regions
            if region.get("role") == "specification_row"
        )
        row = source["row"]
        columns = row["columns"]
        seen.add(fact.source_item_id)
        records.append(
            {
                "model": columns.get("model", ""),
                "manufacturer": columns.get("manufacturer", ""),
                "article": columns.get("article", ""),
                "variant": "",
                "variant_kind": "none",
                "equipment_type": row["name"],
                "object_ref": row["position"],
                "evidence_text": fact.evidence_text,
                "visual_regions": list(fact.visual_regions),
                "parameters": [],
                "status": "needs_review",
                "confidence": 0.8,
                "model_status": "needs_review",
                "manufacturer_status": "unknown",
                "entity_kind": "equipment",
                "extraction_method": fact.extraction_method,
                "specification_item_id": fact.source_item_id,
                "source_row_ids": list(fact.source_row_ids),
                "source_pages": list(fact.source_pages),
                "source_regions": list(fact.source_regions),
                "quantity": row["quantity"],
                "units": row["unit"],
                "under": row["system_header"],
            }
        )
    return tuple(records)


def visual_note_facts(notes, page):
    """Сохраняет прочитанные примечания и штамп как визуальные сведения, без домысливания."""
    from pdrd_analysis_service.domain.physical_identity import valid_box

    result = []
    for index, note in enumerate(notes if isinstance(notes, list) else (), 1):
        if not isinstance(note, dict) or not isinstance(note.get("text"), str):
            continue
        raw_box = note.get("bbox")
        if not isinstance(raw_box, list) or len(raw_box) != 4:
            continue
        box = dict(zip(("x_min", "y_min", "x_max", "y_max"), raw_box, strict=True))
        if not valid_box(box) or not note["text"].strip():
            continue
        fact = document_fact_from_mapping(
            {
                "kind": "reference",
                "subject_type": "document",
                "identifier": f"specification-p{page}",
                "property_type": "specification_note",
                "property_name": "Сведения вне строк спецификации",
                "value_raw": note["text"],
                "evidence_text": note["text"],
                "source_pages": [page],
                "source_item_id": f"visual-spec-p{page}-note-{index}",
                "extraction_method": "vlm_specification_note",
                "visual_regions": [{**box, "confidence": 0.8}],
            }
        )
        if fact:
            result.append(fact)
    return tuple(result)
