# services/analysis-service/src/pdrd_analysis_service/application/equipment_pipeline.py

"""Подготовка EQ-кандидатов с сохранением источника для общего Finalization."""

from collections import defaultdict
from dataclasses import asdict
from typing import Any

from pdrd_analysis_service.domain.equipment import (
    EquipmentFact,
    build_inventory,
    compare_equipment,
    inventory_payload,
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
        key = (
            " ".join(str(identity.get("manufacturer") or "").casefold().split()),
            " ".join(str(identity.get("model") or "").casefold().split()),
            " ".join(str(identity.get("variant") or "").casefold().split()),
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
    if search.get("warning"):
        warnings.append(str(search["warning"])[:300])

    for item in inventory:
        if item.status != "resolved":
            warnings.append(
                f"Оборудование {item.manufacturer} {item.model}: требуется уточнение."
            )
            continue
        result = by_key.get(item.key)
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
                    "experience_query": "",
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
        )
    }
    return {
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
