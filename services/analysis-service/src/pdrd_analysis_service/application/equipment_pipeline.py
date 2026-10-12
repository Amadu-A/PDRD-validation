# services/analysis-service/src/pdrd_analysis_service/application/equipment_pipeline.py

"""Подготовка EQ-кандидатов с сохранением источника для общего Finalization."""

from collections import defaultdict
from dataclasses import asdict, replace
from typing import Any

from pdrd_analysis_service.application.equipment_report import (
    model_report,
    report_statistics,
)
from pdrd_analysis_service.domain.equipment import (
    Comparison,
    EquipmentFact,
    InventoryItem,
    build_inventory,
    compare_equipment,
    equipment_key,
    inventory_payload,
)

_PROPERTY_LABELS = {
    "input_voltage": "входное напряжение",
    "output_voltage": "выходное напряжение",
    "rated_voltage": "номинальное напряжение",
    "input_current": "входной ток",
    "output_current": "выходной ток",
    "rated_current": "номинальный ток",
    "power": "мощность",
    "frequency": "частота",
    "ambient_temperature": "температура окружающей среды",
    "operating_mode": "режим работы",
    "contact_configuration": "конфигурация контактов",
    "trip_curve": "характеристика расцепления",
    "protection_degree": "степень защиты",
    "requires_earthing": "необходимость заземления",
}


def _finding_query(item: InventoryItem, comparison: Comparison, page_type: str) -> str:
    """Готовит предметный запрос N/E, сохраняя неоднозначность EQ сравнения."""
    property_label = _PROPERTY_LABELS.get(
        comparison.property_name, comparison.property_name
    )
    equipment_type = next(
        (
            occurrence.equipment_type
            for occurrence in item.occurrences
            if occurrence.page == comparison.page
            and occurrence.object_ref == comparison.object_ref
            and occurrence.equipment_type
        ),
        "не указан",
    )
    document_type = {"schematic": "схема", "drawing": "чертёж", "table": "таблица"}.get(
        page_type, page_type or "не указан"
    )
    project_value = f"{comparison.project_value} {comparison.project_unit}".strip()
    manufacturer_value = (
        f"{comparison.manufacturer_value} {comparison.manufacturer_unit}".strip()
    )
    variant_label = {"interface": "интерфейс", "model_name": "наименование модели"}.get(
        item.variant_kind, "исполнение"
    )
    return "\n".join(
        (
            "Выбор и применение оборудования в соответствии с техническими характеристиками производителя.",
            f"Тип проектного документа: {document_type}; тип оборудования: {equipment_type}.",
            f"Оборудование: {item.manufacturer} {item.model}; {variant_label}: {item.variant or 'не указано'}; "
            f"обозначение в проекте: {comparison.object_ref or 'не указано'}.",
            f"Проверяемая характеристика: {property_label} ({comparison.property_name}).",
            f"Значение в проекте: {project_value}; значение в документации производителя: {manufacturer_value}.",
            f"Факт на листе проекта: {comparison.project_evidence}",
            f"Результат сопоставления: {comparison.explanation}",
            "Применимость характеристики требует проверки инженером."
            if comparison.status == "needs_review"
            else "Обнаружено расхождение проектного значения с характеристикой производителя.",
            "Техническая документация производителя является EQ основанием; нормативное требование ищется отдельно.",
        )
    )


def compare_equipment_stage(
    pages: list[dict[str, Any]],
    search: dict[str, Any],
) -> dict[str, Any]:
    """Сопоставляет результаты поиска с физическими вхождениями проекта."""
    inventory = build_inventory(pages)
    by_key: dict[tuple[str, str, str], dict] = {}
    for raw in search.get("results", []):
        if not isinstance(raw, dict):
            continue
        identity = raw.get("identity")
        if not isinstance(identity, dict):
            continue
        key = equipment_key(
            *(
                str(identity.get(field) or "")
                for field in ("manufacturer", "model", "variant", "article")
            )
        )
        by_key[key] = raw

    page_types = {
        int(page.get("page_number", 0)): str(page.get("page_type") or "")
        for page in pages
    }
    candidates: dict[int, list[dict]] = defaultdict(list)
    comparisons: list[dict] = []
    warnings: list[str] = []
    diagnostics: list[dict[str, Any]] = []
    models: list[dict] = []
    if search.get("warning"):
        warnings.append(str(search["warning"])[:300])

    for item in inventory:
        result = by_key.get(item.key) or {}
        row = model_report(item, result, [])
        models.append(row)
        verified_brand = result.get("resolved_manufacturer") or (
            result.get("document") or {}
        ).get("manufacturer")
        if item.status != "resolved" and not (
            not item.manufacturer
            and item.manufacturer_status == "unknown"
            and item.model_status == "confirmed"
            and item.confidence >= 0.75
            and verified_brand
            and result.get("status") in {"found", "local"}
        ):
            warnings.append(
                f"Оборудование {item.manufacturer} {item.model}: требуется уточнение."
            )
            continue
        if not item.manufacturer and verified_brand:
            item = replace(item, manufacturer=verified_brand, status="resolved")
        if not result:
            warnings.append(
                f"Документация для {item.manufacturer} {item.model} недоступна."
            )
            continue
        if result.get("warning"):
            warnings.append(str(result["warning"])[:300])
        if result.get("facts_status") == "partial_index":
            warnings.append(
                f"Индексация {item.manufacturer} {item.model} недоступна; использованы текстовые факты."
            )
        document = result.get("document")
        if not isinstance(document, dict) or not document.get("source_id"):
            warnings.append(
                f"Документация для {item.manufacturer} {item.model} не подтверждена."
            )
            continue
        if document.get("revision_ambiguous"):
            warnings.append(
                f"Для {item.manufacturer} {item.model} сохранено несколько ревизий: "
                "сравнение требует проверки применимости."
            )
        raw_facts = result.get("facts", [])
        facts = [
            EquipmentFact(
                property_name=str(fact.get("property_name") or ""),
                value_raw=str(fact.get("value_raw") or ""),
                unit_raw=str(fact.get("unit_raw") or ""),
                role=str(fact.get("role") or ""),
                current_type=str(fact.get("current_type") or ""),
                mode=str(fact.get("mode") or ""),
                variant=str(fact.get("variant") or ""),
                source_id=str(fact.get("document_source_id") or ""),
                source_page=int(fact.get("page") or 0),
                snippet=str(fact.get("snippet") or ""),
                confidence=(
                    0.0
                    if document.get("revision_ambiguous")
                    else float(fact.get("confidence") or 0)
                ),
                applicability=str(fact.get("applicability") or ""),
                phase=str(fact.get("phase") or ""),
                condition=str(fact.get("condition") or ""),
                configuration=str(fact.get("configuration") or ""),
                value_type=str(fact.get("value_type") or "number"),
            )
            for fact in raw_facts
            if isinstance(fact, dict)
            and fact.get("document_source_id") == document["source_id"]
            and int(fact.get("page") or 0) > 0
            and fact.get("snippet")
        ]
        if not facts:
            warnings.append(
                f"Для {item.manufacturer} {item.model} нужны подтверждённые EQ Facts."
            )
            continue
        model_comparisons = compare_equipment(item, facts)
        row.update(
            model_report(item, result, [asdict(part) for part in model_comparisons])
        )
        if not model_comparisons and any(part.parameters for part in item.occurrences):
            diagnostics.append(
                {
                    "manufacturer": item.manufacturer,
                    "model": item.model,
                    "variant": item.variant,
                    "status": "not_comparable",
                    "reason": "Нет характеристик с совместимыми свойством, режимом и единицами.",
                }
            )
            warnings.append(
                f"{item.manufacturer} {item.model}: нет сравнимых характеристик."
            )
        for comparison in model_comparisons:
            comparisons.append(asdict(comparison))
            if comparison.status not in {"mismatch_candidate", "needs_review"}:
                continue
            confirmed = comparison.status == "mismatch_candidate"
            basis = {
                "source_id": comparison.source_id,
                "manufacturer": item.manufacturer,
                "model": item.model,
                "variant": item.variant,
                "property_name": comparison.property_name,
                "value_raw": comparison.manufacturer_value,
                "unit_raw": comparison.manufacturer_unit,
                "page": comparison.source_page,
                "snippet": comparison.manufacturer_evidence,
                "document_revision": str(document.get("revision") or ""),
                "sha256": str(document.get("sha256") or ""),
                "source_url": str(document.get("final_url") or ""),
                "trust_status": str(document.get("trust_status") or ""),
            }
            project_text = (
                f"{comparison.project_value} {comparison.project_unit}".strip()
            )
            maker_text = (
                f"{comparison.manufacturer_value} {comparison.manufacturer_unit}"
            ).strip()
            candidates[comparison.page].append(
                {
                    "finding_id": comparison.candidate_id,
                    "page": comparison.page,
                    "page_type": page_types.get(comparison.page, ""),
                    "category": "equipment",
                    "severity": "warning",
                    "status": "confirmed" if confirmed else "needs_review",
                    "comment": (
                        f"{item.manufacturer} {item.model}: "
                        f"{comparison.property_name} в проекте {project_text}; "
                        f"документация производителя указывает {maker_text}. "
                        f"{comparison.explanation}"
                    ),
                    "evidence": comparison.project_evidence,
                    "recommendation_draft": (
                        "Проверить выбранное исполнение и привести проектное "
                        "значение в соответствие с документацией производителя."
                    ),
                    "confidence": 0.9 if confirmed else 0.65,
                    "normative_source_ids": [],
                    "basis": "Техническая документация производителя",
                    "basis_sources": [],
                    "experience_query": _finding_query(
                        item, comparison, page_types.get(comparison.page, "")
                    ),
                    "technical_assignment_source_ids": [],
                    "technical_assignment_basis_sources": [],
                    "user_package_source_ids": [],
                    "user_package_basis_sources": [],
                    "visual_regions": list(comparison.visual_regions),
                    "origin_assertions": [],
                    "object_ref": comparison.object_ref,
                    "evidence_locations": [],
                    "document_context_source_ids": [],
                    "document_context_basis_sources": [],
                    "equipment_documentation_source_ids": [comparison.source_id],
                    "equipment_documentation_basis_sources": [basis],
                    "equipment_details": {
                        "manufacturer": item.manufacturer,
                        "model": item.model,
                        "variant": item.variant,
                        "article": item.article,
                        "variant_kind": item.variant_kind,
                        "object_ref": comparison.object_ref,
                        "property_name": comparison.property_name,
                        "project_value": comparison.project_value,
                        "project_unit": comparison.project_unit,
                        "manufacturer_value": comparison.manufacturer_value,
                        "manufacturer_unit": comparison.manufacturer_unit,
                        "comparison_status": comparison.status,
                    },
                }
            )

    metrics = {
        key: sum(
            float(result.get("metrics", {}).get(key) or 0) for result in by_key.values()
        )
        for key in (
            "duration_ms",
            "vision_calls",
            "vision_cache_hits",
            "prompt_tokens",
            "output_tokens",
            "queries",
            "downloads",
            "verified_documents",
        )
    }
    return {
        "models": models,
        "statistics": report_statistics(models, inventory),
        "background": bool(search.get("background")),
        "revision": int(search.get("revision") or 0),
        "search_status": str(search.get("status") or "incomplete"),
        "policy": search.get("policy") or {},
        "metrics": metrics,
        "inventory": inventory_payload(inventory),
        "comparisons": comparisons,
        "candidates_by_page": {str(page): rows for page, rows in candidates.items()},
        "warnings": warnings,
        "diagnostics": diagnostics,
        "status": (
            "incomplete"
            if warnings or search.get("status") != "completed"
            else "completed"
        ),
    }
