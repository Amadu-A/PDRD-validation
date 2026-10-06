# services/experience-service/src/pdrd_experience_service/domain/catalog_identity.py

"""Семантическая идентичность примера между прогонами одного исходного PDF.

Время, job/finding UUID и положение карточки не меняют найденную ошибку.
Решение, происхождение, формулировка и область ошибки отличают новые примеры.
"""

import hashlib
import json
from dataclasses import asdict

from pdrd_experience_service.domain.experience_selection import ExperienceCandidate


def content_key(source: ExperienceCandidate) -> str:
    """Хеширует исходные инженерные данные, сохраняя смысловой регистр текста."""
    return snapshot_content_key(
        {
            "source_sha256": source.source_sha256,
            "page_number": source.page_number,
            "origin": source.origin.value,
            "tag": source.tag,
            "decision": source.decision.value,
            "reason_category": source.reason_category,
            "comment": source.comment,
            "original_text": source.original_text,
            "text": source.text,
            "normative_basis": source.normative_basis,
            "original_basis": source.original_basis,
            "issue_regions": [
                {
                    "x_min": box.x_min,
                    "y_min": box.y_min,
                    "x_max": box.x_max,
                    "y_max": box.y_max,
                }
                for box in source.issue_regions
            ],
            "proposed_regions": [asdict(item) for item in source.proposed_regions],
            "display_regions": [asdict(item) for item in source.display_regions]
            if source.display_regions is not None
            else None,
        }
    )


def snapshot_content_key(source: dict) -> str:
    """Раздел и provenance не создают дубль; отличающиеся области — создают."""
    payload = {
        key: source.get(key, "")
        for key in (
            "source_sha256",
            "page_number",
            "origin",
            "tag",
            "decision",
            "original_text",
            "text",
            "original_basis",
            "normative_basis",
        )
    }
    for key in ("original_text", "text", "original_basis", "normative_basis"):
        payload[key] = " ".join(payload[key].split())
    payload["issue_regions"] = sorted(
        [
            [round(float(box[key]), 6) for key in ("x_min", "y_min", "x_max", "y_max")]
            for box in source.get("issue_regions", [])
        ]
    )

    def coordinates(boxes):
        return sorted(
            [
                [
                    round(float(box[key]), 6)
                    for key in ("x_min", "y_min", "x_max", "y_max")
                ]
                for box in boxes
            ]
        )

    original = coordinates(
        [item["bbox"] for item in source.get("proposed_regions", [])]
    )
    display = source.get("display_regions")
    if original and original != payload["issue_regions"]:
        payload["original_regions"] = original
    if display is not None and coordinates(display) != payload["issue_regions"]:
        payload["display_regions"] = coordinates(display)
    if source.get("reason_category") is not None:
        payload["reason_category"] = source["reason_category"]
        payload["comment"] = source.get("comment", "")
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
