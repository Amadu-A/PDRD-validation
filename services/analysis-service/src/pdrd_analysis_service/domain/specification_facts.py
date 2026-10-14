# services/analysis-service/src/pdrd_analysis_service/domain/specification_facts.py

"""Представления D и базовых сведений из единственного источника строк спецификации."""

from dataclasses import replace

from pdrd_analysis_service.domain.analysis import PageFacts
from pdrd_analysis_service.domain.document_context import (
    DocumentFact,
    document_fact_from_mapping,
)


def specification_document_facts(
    specification: dict, page_number: int
) -> tuple[DocumentFact, ...]:
    """Сохраняет все явно записанные значения, а не лимит генерируемых VLM-фактов.

    Идентичность задаётся исходной позицией, не названием модели. Разные изделия
    одной модели не становятся одним объектом сравнения. Caption не разбирается.
    """
    facts, seen = [], set()
    rows = {row["row_id"]: row for row in specification["raw_rows"]}
    for item in specification["items"]:
        regions = [
            {**region, "confidence": 1.0}
            for region in item["source_regions"]
            if region["page"] == page_number
        ]
        base = {
            "kind": "equipment",
            "subject_type": "specification_item",
            "subject_name": item["name"],
            "identifier": item["item_id"],
            "scope_system": item["under"],
            "scope_segment": item["item_id"],
            "scope_condition": item.get("variant", ""),
            "table_title": "Спецификация оборудования, изделий и материалов",
            "table_id": next(
                (
                    rows[row_id].get("table_id", "")
                    for row_id in item["row_ids"]
                    if row_id in rows
                ),
                "",
            ),
            "row_label": item["position"],
            "source_item_id": item["item_id"],
            "source_row_ids": item["row_ids"],
            "source_pages": item["source_pages"],
            "source_regions": item["source_regions"],
            "visual_regions": regions,
            "extraction_method": "pdf_table",
        }
        values = [
            ("name", "equipment_name", "Наименование", ""),
            ("manufacturer", "equipment_manufacturer", "Производитель", ""),
            ("article", "equipment_article", "Артикул", ""),
            ("quantity", "quantity", "Количество", item["units"]),
        ]
        if not item.get("needs_review"):
            values.extend(
                [
                    ("model", "equipment_model", "Модель", ""),
                    ("variant", "equipment_variant", "Исполнение", ""),
                ]
            )
        for field, kind, label, unit in values:
            if item.get(field):
                raw = {
                    **base,
                    "kind": kind,
                    "property_type": kind,
                    "property_name": label,
                    "column_label": field,
                    "value_raw": item[field],
                    "unit_raw": unit,
                    "evidence_text": item[field] + (" " + unit if unit else ""),
                }
                _append_fact(facts, seen, raw)
        for parameter in item.get("parameters", []):
            if (
                parameter.get("evidence_status") != "confirmed"
                or not parameter.get("value_raw")
                or not parameter.get("evidence_text")
            ):
                continue
            raw = {
                **base,
                "kind": "equipment_parameter",
                "property_type": parameter["property_name"],
                "property_name": parameter["property_name"],
                "column_label": "name",
                "value_raw": parameter["value_raw"],
                "unit_raw": parameter.get("unit_raw", ""),
                "evidence_text": parameter["evidence_text"],
                "relation": parameter.get("role", ""),
                "scope_operating_mode": parameter.get("mode", ""),
                "scope_condition": "; ".join(
                    str(value)
                    for value in [
                        item.get("variant"),
                        *(
                            parameter.get(key)
                            for key in (
                                "role",
                                "current_type",
                                "phase",
                                "configuration",
                                "condition",
                            )
                        ),
                    ]
                    if value
                ),
            }
            _append_fact(facts, seen, raw)
    return tuple(facts)


def _append_fact(facts: list, seen: set, raw: dict) -> None:
    """Одно значение одной строки не становится двумя независимыми доказательствами."""
    fact = document_fact_from_mapping(raw)
    if fact is None:
        return
    key = (
        fact.source_item_id,
        fact.property_type,
        fact.property_name,
        fact.value_raw,
        fact.unit_raw,
        fact.scope_operating_mode,
        fact.scope_condition,
    )
    if key not in seen:
        seen.add(key)
        facts.append(fact)


def specification_page_facts(
    specification: dict, *, document_facts: tuple[DocumentFact, ...] = ()
) -> PageFacts:
    """Формирует сведения листа из полей, заголовков и позиций без выдуманных связей."""
    items = specification["items"]
    objects = tuple(
        "; ".join(
            value for value in (item["name"], item["model"], item["variant"]) if value
        )
        for item in items
    )
    labels = tuple(
        dict.fromkeys(
            [
                *(item["position"] for item in items if item["position"]),
                *(
                    [specification["project_code"]]
                    if specification["project_code"]
                    else []
                ),
            ]
        )
    )
    return PageFacts(
        discipline="",
        page_type="table",
        summary=(
            "Спецификация оборудования, изделий и материалов: "
            f"{len(items)} позиций, {specification['raw_row_count']} исходных строк."
        ),
        objects=objects,
        connections=(),
        labels=labels,
        normative_queries=(),
        document_facts=document_facts,
        equipment_specification=specification,
    )


def merge_specification_facts(table: PageFacts, visual: PageFacts) -> PageFacts:
    """Добавляет нетабличные сведения, не теряя исходные строки и значения таблицы."""
    item_ids = {item["item_id"] for item in table.equipment_specification["items"]}
    table_regions = table.equipment_specification.get("page_content", {}).get(
        "table_regions", []
    )
    new_facts = tuple(
        fact
        for fact in visual.document_facts
        if fact.source_item_id not in item_ids
        and not (
            fact.visual_regions
            and all(
                any(
                    region["x_min"] >= box["x_min"]
                    and region["y_min"] >= box["y_min"]
                    and region["x_max"] <= box["x_max"]
                    and region["y_max"] <= box["y_max"]
                    for box in table_regions
                )
                for region in fact.visual_regions
            )
        )
    )
    return replace(
        visual,
        objects=tuple(dict.fromkeys([*table.objects, *visual.objects])),
        labels=tuple(dict.fromkeys([*table.labels, *visual.labels])),
        document_facts=(*table.document_facts, *new_facts),
        equipment_specification=table.equipment_specification,
    )
