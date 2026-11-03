# services/analysis-service/src/pdrd_analysis_service/application/equipment_report.py

"""Формирует отдельный технический результат по каждой уникальной модели."""

from dataclasses import asdict

from pdrd_analysis_service.domain.equipment import (
    InventoryItem,
    equipment_property_name,
)


def model_report(item: InventoryItem, result: dict, comparisons: list[dict]) -> dict:
    """Не выдаёт наличие документа или incomplete за подтверждённое нарушение."""
    document = result.get("document") or {}
    statuses = {row["status"] for row in comparisons}
    if "mismatch_candidate" in statuses:
        status, reason = (
            "mismatch",
            "Подтверждено расхождение характеристики; требуется решение инженера.",
        )
    elif comparisons and statuses == {"matched"} and document.get("source_id"):
        status, reason = (
            "confirmed",
            "Документ и сравнимые характеристики подтверждены.",
        )
    elif item.model_status != "confirmed":
        status, reason = "needs_identification", "Маркировка модели требует уточнения."
    elif result.get("status") == "not_checked" or not result:
        status, reason = "not_checked", "Поиск ещё не выполнялся."
    else:
        status, reason = (
            "incomplete",
            str(
                result.get("warning")
                or "Для сравнения требуются дополнительные данные."
            )[:300],
        )
    code = str(result.get("reason_code") or "")
    if not code and not document.get("source_id"):
        code = "not_checked" if status == "not_checked" else "document_not_found"
    if document.get("revision_ambiguous"):
        code, reason = (
            "revision_ambiguous",
            "Несколько редакций документа; применимость требует проверки.",
        )
    elif document.get("trust_status") == "unverified_user_override" and comparisons:
        status, code, reason = (
            "incomplete",
            "source_unverified",
            "Характеристики сопоставлены по разрешённому в этом задании источнику; достоверность требует проверки.",
        )
    elif document.get("source_id") and not comparisons:
        if not any(
            parameter.get("evidence_status") != "needs_review"
            for part in item.occurrences
            for parameter in part.parameters
        ):
            status, code, reason = (
                "no_data",
                "project_properties_missing",
                "В проекте нет подтверждённых характеристик для сравнения.",
            )
        elif not code:
            code = "facts_unavailable"
    if document.get("applicability") == "technical_reference":
        status = "no_data" if not comparisons else "incomplete"
        reason = "Справочник найден. Конкретная поставка и проектное обоснование не подтверждены."
    return {
        "identity": asdict(item) | {"occurrences": None},
        "manufacturer": result.get("resolved_manufacturer")
        or document.get("manufacturer")
        or item.manufacturer,
        "model": item.model,
        "variant": item.variant,
        "variant_kind": item.variant_kind,
        "article": item.article,
        "item_type": item.item_type,
        "search_name": item.search_name,
        "search_reference": item.search_reference,
        "status": status,
        "search_status": result.get("status", "not_checked"),
        "reason_code": code,
        "reason": reason,
        "metrics": result.get("metrics") or {},
        "attempts": result.get("attempts") or [],
        "candidates": result.get("candidates") or [],
        "stage_status": "comparison_completed"
        if comparisons
        else _search_stage(result),
        "facts": result.get("facts") or [],
        "document": document or None,
        "manufacturer_site": result.get("manufacturer_site"),
        "comparisons": comparisons,
        "pages": sorted({part.page for part in item.occurrences}),
        "occurrences": [asdict(part) for part in item.occurrences],
    }


def _search_stage(result: dict) -> str:
    """Читает старые задания без вымышленного успеха или отрицания выполненного поиска."""
    if result.get("stage_status") and result["stage_status"] != "not_searched":
        return result["stage_status"]
    if result.get("status") in {"queued", "running"}:
        return "search_running"
    if (result.get("document") or {}).get("source_id"):
        return "facts_extracted" if result.get("facts") else "document_found"
    code = str(result.get("reason_code") or "")
    if code.endswith("budget_exhausted"):
        return "budget_exhausted"
    if code in {
        "tls_error",
        "dns_error",
        "network_error",
        "operation_timeout",
        "file_too_large",
        "http_error",
    }:
        return "download_failed"
    if code.endswith("mismatch") or code in {
        "manufacturer_unknown",
        "reference_not_applicable",
    }:
        return "document_rejected"
    if code == "document_not_found":
        return "document_not_found"
    if result.get("candidates"):
        return "candidates_found"
    return (
        "comparison_unavailable"
        if (result.get("metrics") or {}).get("queries")
        else "not_searched"
    )


def report_statistics(models: list[dict], inventory: tuple[InventoryItem, ...]) -> dict:
    """Разделяет распознанные записи, уникальные модели и фактические операции."""
    return {
        "recognized_records": sum(len(item.occurrences) for item in inventory),
        "unique_models": len(inventory),
        "searched_models": sum(
            float(row["metrics"].get("queries") or 0) > 0 for row in models
        ),
        "saved_documents": len(
            {row["document"]["source_id"] for row in models if row.get("document")}
        ),
        **{
            status: sum(row["status"] == status for row in models)
            for status in (
                "confirmed",
                "mismatch",
                "incomplete",
                "not_checked",
                "needs_identification",
                "no_data",
            )
        },
    }


def position_report(models: list[dict], specification: dict) -> list[dict]:
    """Показывает каждую исходную позицию при общем поиске одинаковых изделий."""
    by_row = {
        identifier: model
        for model in models
        for part in model.get("occurrences", [])
        for identifier in part.get("source_row_ids", [])
    }
    rows = []
    for item in specification.get("structured_items", []):
        base = next(
            (by_row[key] for key in item.get("row_ids", []) if key in by_row), {}
        )
        comparisons = [
            value
            for value in base.get("comparisons", [])
            if value.get("page") in item.get("source_pages", [])
            and value.get("object_ref") == (item.get("position") or item.get("item_id"))
        ]
        row_status = base.get("status", "not_checked")
        row_reason, row_code = base.get("reason", ""), base.get("reason_code", "")
        occurrences = [
            part
            for part in base.get("occurrences", [])
            if set(part.get("source_row_ids", [])) & set(item.get("row_ids", []))
        ]
        project_props = {
            equipment_property_name(str(parameter.get("property_name") or ""))
            for part in occurrences
            for parameter in part.get("parameters", [])
            if parameter.get("evidence_status") != "needs_review"
        } - {""}
        fact_props = {
            equipment_property_name(str(fact.get("property_name") or ""))
            for fact in base.get("facts", [])
        } - {""}
        compared = {value.get("property_name") for value in comparisons}
        property_checks = [
            {
                "property_name": prop,
                "status": "project_property_missing"
                if prop not in project_props
                else "manufacturer_property_missing"
                if prop not in fact_props
                else "comparison_completed"
                if prop in compared
                else "conditions_not_comparable",
            }
            for prop in sorted(project_props | fact_props)
        ]
        statuses = {value["status"] for value in comparisons}
        if (
            comparisons
            and (base.get("document") or {}).get("trust_status") == "trusted"
        ):
            row_status = (
                "mismatch"
                if "mismatch_candidate" in statuses
                else "confirmed"
                if statuses == {"matched"}
                else "incomplete"
            )
        elif (base.get("document") or {}).get("source_id") and not comparisons:
            row_status = "no_data"
            row_code = (
                "project_properties_missing"
                if not project_props
                else "facts_unavailable"
            )
            row_reason = (
                "В этой позиции нет подтверждённых проектных параметров для сравнения."
                if not project_props
                else "Сопоставимость параметров этой позиции не установлена."
            )
        rows.append(
            {
                **base,
                "equipment_id": item["item_id"],
                "specification_caption": item.get("Caption", item.get("name", "")),
                "project_position": item.get("position", ""),
                "quantity": item.get("quantity", ""),
                "units": item.get("units", ""),
                "pages": item.get("source_pages", []),
                "comparisons": comparisons,
                "status": row_status,
                "reason": row_reason,
                "reason_code": row_code,
                "stage_status": "comparison_completed"
                if comparisons
                else "comparison_unavailable"
                if (base.get("document") or {}).get("source_id")
                else base.get("stage_status", "not_searched"),
                "property_checks": property_checks,
                "occurrences": occurrences,
            }
        )
    rows.extend(
        {**model, "occurrences": parts}
        for model in models
        if (
            parts := [
                part
                for part in model.get("occurrences", [])
                if not part.get("source_row_ids")
            ]
        )
    )
    return rows or models
