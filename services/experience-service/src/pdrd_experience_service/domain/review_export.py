# services/experience-service/src/pdrd_experience_service/domain/review_export.py

"""Проекция утверждённого Review для PDF без рендеринга и учебного хранения.

Текст сохраняется полностью. Непроверенная VLM-геометрия никогда не становится
рамкой PDF; номер замечания одинаков на листе и в текстовом приложении.
"""

import hashlib
import json
from dataclasses import asdict, dataclass

from pdrd_experience_service.domain.review import Origin, Rectangle, ReviewSession


@dataclass(frozen=True, slots=True)
class AreaStatus:
    """Версия подтверждения, включая отозванное или устаревшее состояние."""

    finding_id: str
    revision: int
    valid: bool
    regions: tuple[Rectangle, ...]


def export_manifest(review: ReviewSession, areas: tuple[AreaStatus, ...]) -> dict:
    """Отбирает только принятые замечания текущей утверждённой редакции."""
    approved = review.accepted_for_pdf()
    confirmed = {area.finding_id: area for area in areas if area.valid}
    findings = []
    for number, finding in enumerate(approved.findings, start=1):
        area = confirmed.get(finding.finding_id)
        if finding.origin is Origin.MANUAL:
            regions = (finding.issue_box,) if finding.issue_box is not None else ()
        else:
            regions = area.regions if area is not None else ()
        findings.append(
            {
                "number": number,
                "finding_id": finding.finding_id,
                "page_number": finding.page_number,
                "origin": finding.origin.value,
                "experience_tag": finding.experience_tag,
                "text": finding.text,
                "normative_basis": finding.normative_basis,
                "regions": [asdict(box) for box in regions],
                "callout_box": asdict(finding.callout_box)
                if finding.callout_box
                else None,
            }
        )
    payload = {
        "job_id": str(review.job_id),
        "document_id": str(review.document_id),
        "source_filename": review.source_filename,
        "source_sha256": review.source_sha256,
        "revision": review.revision,
        "findings": findings,
    }
    # Отзыв области меняет ключ PDF даже без изменения редакции Review.
    signature = {
        **payload,
        "areas": [asdict(area) for area in sorted(areas, key=lambda a: a.finding_id)],
    }
    payload["digest"] = hashlib.sha256(
        json.dumps(
            signature,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return payload
