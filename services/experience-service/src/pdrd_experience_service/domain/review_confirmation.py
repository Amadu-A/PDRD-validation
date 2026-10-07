# services/experience-service/src/pdrd_experience_service/domain/review_confirmation.py

"""Связывает явное решение инженера с текущими областями серверного Review.

Пустая локализация остаётся пустой. Правка геометрии не меняет текстовый тег;
проверенной область становится при принятии, а не при получении JSON из браузера.
"""

from datetime import datetime

from pdrd_experience_service.domain.area_confirmation import (
    AreaConfirmation,
    ConfirmationMode,
    build_confirmation,
)
from pdrd_experience_service.domain.review import Decision, Origin, ReviewSession


def reviewed_area_confirmations(
    *,
    review: ReviewSession,
    actor: str,
    at: datetime,
    finding_id: str | None = None,
) -> tuple[AreaConfirmation, ...]:
    """Подтверждает принятые VLM-области; Gold и замечания без рамок не выдумывает."""
    result = []
    for finding in review.findings:
        if (
            finding.origin is not Origin.VLM
            or finding.decision is not Decision.ACCEPTED
            or (finding_id is not None and finding.finding_id != finding_id)
        ):
            continue
        regions = finding.display_regions
        if regions is None:
            regions = tuple(area.bbox for area in finding.proposed_regions)
        if not regions and not any(
            location.proposed_regions for location in finding.evidence_locations
        ):
            continue
        result.append(
            build_confirmation(
                session=review,
                finding_id=finding.finding_id,
                regions=regions,
                mode=ConfirmationMode.DECISION,
                note="",
                actor=actor,
                at=at,
                expected_review_revision=review.revision,
            )
        )
    return tuple(result)
