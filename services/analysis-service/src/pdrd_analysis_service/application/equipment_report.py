# services/analysis-service/src/pdrd_analysis_service/application/equipment_report.py

"""Формирует отдельный технический результат по каждой уникальной модели."""

from dataclasses import asdict

from pdrd_analysis_service.domain.equipment import InventoryItem


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
    return {
        "identity": asdict(item) | {"occurrences": None},
        "manufacturer": result.get("resolved_manufacturer")
        or document.get("manufacturer")
        or item.manufacturer,
        "model": item.model,
        "variant": item.variant,
        "variant_kind": item.variant_kind,
        "article": item.article,
        "status": status,
        "search_status": result.get("status", "not_checked"),
        "reason_code": code,
        "reason": reason,
        "metrics": result.get("metrics") or {},
        "attempts": result.get("attempts") or [],
        "document": document or None,
        "manufacturer_site": result.get("manufacturer_site"),
        "comparisons": comparisons,
        "pages": sorted({part.page for part in item.occurrences}),
        "occurrences": [asdict(part) for part in item.occurrences],
    }


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
