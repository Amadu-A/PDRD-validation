# services/analysis-service/src/pdrd_analysis_service/domain/atomic_normative_status.py

"""Проверка снимка нормативного статуса без сетевых запросов и пересказа норм."""

from datetime import date


def atomic_currency(source, assessed_on: str) -> tuple[str, tuple[dict, ...]]:
    """Принимает только датированные свидетельства того же SHA, редакции и пункта."""
    row = source.atomic_requirement or {}
    if not isinstance(row, dict) or not isinstance(row.get("normative_status"), dict):
        return "unknown", ()
    status = row["normative_status"]
    if (
        not isinstance(row.get("clause"), str)
        or not isinstance(row.get("subclause", ""), str)
        or not isinstance(row.get("status_evidence", []), list)
    ):
        return "unknown", ()
    target = row.get("clause", "") + (
        "." + row["subclause"] if row.get("subclause") else ""
    )
    if (
        not target
        or not row.get("edition")
        or not row.get("document_code")
        or row.get("point_id") != source.point_id
        or row.get("requirement_id") != source.source_id
        or status.get("assessed_on") != assessed_on
        or status.get("reason_code") == "status_evidence_changed"
    ):
        return "unknown", ()
    proofs = []
    try:
        on = date.fromisoformat(assessed_on)
    except (ValueError, TypeError):
        return "unknown", ()
    for evidence in row.get("status_evidence", []):
        if not isinstance(evidence, dict):
            continue
        try:
            valid = (
                evidence.get("edition_status")
                in {"current", "withdrawn", "disputed", "unknown"}
                and evidence.get("requirement_binding")
                in {"mandatory", "voluntary", "disputed", "unknown"}
                and all(
                    isinstance(row.get(key), str) and row[key]
                    for key in (
                        "document_id",
                        "source_sha256",
                        "document_code",
                        "edition",
                    )
                )
                and all(
                    evidence.get(key) == row.get(key)
                    for key in (
                        "document_id",
                        "source_sha256",
                        "document_code",
                        "edition",
                    )
                )
                and evidence.get("evidence_id") in status.get("evidence_ids", [])
                and date.fromisoformat(evidence["valid_from"])
                <= on
                <= date.fromisoformat(evidence["valid_until"])
                and date.fromisoformat(evidence["confirmed_on"]) <= on
                and bool(
                    evidence.get("verified_by")
                    and evidence.get("source_url")
                    and evidence.get("source_quote")
                )
                and any(
                    scope == "*" or target == scope or target.startswith(scope + ".")
                    for scope in evidence.get("clause_scope", [])
                )
            )
        except (KeyError, TypeError, ValueError):
            valid = False
        if valid:
            proofs.append(evidence)
    if not proofs:
        return "unknown", ()
    editions = {item["edition_status"] for item in proofs} - {"unknown"}
    bindings = {item["requirement_binding"] for item in proofs} - {"unknown"}
    proof = (
        {
            "source_id": source.source_id,
            "quote": source.text,
            "page": source.page,
            "as_of": assessed_on,
            "status_evidence": proofs,
        },
    )
    if len(editions) > 1 or len(bindings) > 1 or "disputed" in editions | bindings:
        return "conflicting_status", proof
    if editions == {"withdrawn"}:
        return "cancelled", proof
    if (
        editions == {"current"}
        and bindings == {"mandatory"}
        and row.get("modality") == "mandatory"
    ):
        return "current_mandatory", proof
    return "current_binding_unknown" if editions == {"current"} else "unknown", proof
