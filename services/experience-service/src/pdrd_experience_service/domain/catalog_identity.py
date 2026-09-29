# services/experience-service/src/pdrd_experience_service/domain/catalog_identity.py

"""Семантическая идентичность примера между прогонами одного исходного PDF.

Время, job/finding UUID и положение карточки не меняют найденную ошибку.
Решение, происхождение, формулировка и область ошибки отличают новые примеры.
"""

import hashlib
import json

from pdrd_experience_service.domain.experience_selection import ExperienceCandidate


def content_key(source: ExperienceCandidate) -> str:
    """Хеширует исходные инженерные данные, сохраняя смысловой регистр текста."""
    return snapshot_content_key(
        {
            "source_sha256": source.source_sha256,
            "section_id": source.section_id,
            "page_number": source.page_number,
            "origin": source.origin.value,
            "tag": source.tag,
            "decision": source.decision.value,
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
        }
    )


def snapshot_content_key(source: dict) -> str:
    """Обратная совместимость: старый снимок без раздела остаётся неопределённым."""
    payload = {
        key: source.get(key, "")
        for key in (
            "source_sha256",
            "section_id",
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
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
