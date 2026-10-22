# services/analysis-service/src/pdrd_analysis_service/application/candidate_fates.py

"""Судьба исходных кандидатов после финализации, отдельно от пользовательского PDF."""

from collections import Counter
from dataclasses import asdict


def final_candidate_fates(originals, findings, rejected):
    """Связывает вход с результатом, не дублируя тяжёлые доказательства в n8n.

    Полный отвергнутый вход сохраняется один раз. Сохранённый/объединённый
    кандидат ссылается на итог с источниками, областями и историей происхождения.
    Пропуск решения не считается отклонением.
    """
    kept = {item.finding_id: item for item in findings}
    targets = {
        origin["finding_id"]: item.finding_id
        for item in findings
        for origin in item.origin_assertions
        if origin.get("origin") == "merged_finding" and origin.get("finding_id")
    }
    rejections = {item["finding_id"]: item for item in rejected}
    records = []
    for candidate in originals:
        key = candidate.finding_id
        rejection = rejections.get(key)
        if key in kept or key in targets:
            disposition = "saved" if key in kept else "merged"
            reason = "retained" if key in kept else "same_engineering_assertion"
        elif rejection:
            audit = next(
                (
                    origin
                    for origin in reversed(
                        rejection.get("candidate", {}).get("origin_assertions", [])
                    )
                    if origin.get("origin") == "engineering_evidence_audit"
                ),
                {},
            )
            disposition = (
                "unassessed"
                if (
                    rejection.get("disposition") == "internal_diagnostic"
                    and audit.get("status") != "counterevidence"
                )
                else "refuted"
            )
            reason = rejection.get("reason_code") or rejection.get(
                "reason", "explicit_rejection"
            )
        else:
            disposition, reason = "unassessed", "missing_final_decision"
        if key in kept or key in targets:
            snapshot = {
                "candidate_ref": {
                    "collection": "findings",
                    "finding_id": key if key in kept else targets[key],
                },
                "original": {
                    "page": candidate.page,
                    "comment": candidate.comment,
                    "evidence": candidate.evidence,
                    "recommendation_draft": candidate.recommendation_draft,
                    "status": candidate.status,
                },
            }
        elif rejection and "candidate" in rejection:
            snapshot = {
                "candidate_ref": {"collection": "rejected_findings", "finding_id": key}
            }
        else:
            snapshot = {"candidate": asdict(candidate)}
        records.append(
            {
                "finding_id": key,
                "candidate_ids": list(
                    dict.fromkeys(
                        str(origin["candidate_id"])
                        for origin in candidate.origin_assertions
                        if origin.get("candidate_id")
                    )
                ),
                "disposition": disposition,
                "reason_code": reason,
                "resulting_finding_id": key if key in kept else targets.get(key),
                **snapshot,
                **(
                    {
                        "decision": {
                            key: value
                            for key, value in rejection.items()
                            if key != "candidate"
                        }
                    }
                    if rejection
                    else {}
                ),
            }
        )
    return {
        "counts": dict(Counter(row["disposition"] for row in records)),
        "candidates": records,
    }
