# services/experience-service/src/pdrd_experience_service/domain/experience_selection.py

"""Отбор итоговых решений каталога и локализованных примеров для обучения."""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pdrd_experience_service.domain.rejection_feedback import validate_feedback
from pdrd_experience_service.domain.review import (
    Decision,
    Origin,
    ProposedRegion,
    Rectangle,
    ReviewedFinding,
    ReviewError,
    ReviewSession,
)


class LearningUse(StrEnum):
    """Допустимое последующее использование отобранного примера."""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEEDS_ADJUDICATION = "needs_adjudication"


@dataclass(frozen=True, slots=True)
class ConfirmedFindingArea:
    """Области VLM finding, принятые инженером и проверенные сервером."""

    job_id: UUID
    finding_id: str
    page_number: int
    regions: tuple[Rectangle, ...]
    confirmed_by: str
    confirmed_at: datetime

    def __post_init__(self) -> None:
        """Отсекает незаполненные и ошибочные подтверждения координат."""
        if (
            not isinstance(self.finding_id, str)
            or not self.finding_id.strip()
            or self.finding_id.startswith("manual:")
            or not isinstance(self.page_number, int)
            or isinstance(self.page_number, bool)
            or self.page_number < 1
            or not isinstance(self.regions, tuple)
            or not self.regions
            or any(not isinstance(region, Rectangle) for region in self.regions)
            or not isinstance(self.confirmed_by, str)
            or not self.confirmed_by.strip()
            or not isinstance(self.confirmed_at, datetime)
            or self.confirmed_at.tzinfo is None
            or self.confirmed_at.utcoffset() is None
        ):
            raise ReviewError("Неверное подтверждение области замечания.")


@dataclass(frozen=True, slots=True)
class ExperienceCandidate:
    """Готовый к сохранению пример; crop создаётся позже из исходного PDF."""

    example_key: str
    job_id: UUID
    document_id: UUID
    source_filename: str
    source_sha256: str
    approved_revision: int
    finding_id: str
    finding_revision: int
    origin: Origin
    tag: str
    decision: Decision
    learning_use: LearningUse
    page_number: int
    issue_regions: tuple[Rectangle, ...]
    callout_box: Rectangle | None
    original_text: str
    text: str
    original_basis: str
    normative_basis: str
    created_by: str
    updated_by: str
    confirmed_by: str
    confirmed_at: datetime
    section_id: str = ""
    section_title: str = ""
    proposed_regions: tuple[ProposedRegion, ...] = ()
    display_regions: tuple[Rectangle, ...] | None = None
    area_source: str = "engineer_confirmed"
    reason_category: str | None = None
    comment: str = ""

    def __post_init__(self) -> None:
        """Проверяет объяснение отказа, сохраняя совместимость со старым каталогом."""
        try:
            reason, comment = validate_feedback(
                rejected=self.decision is Decision.REJECTED,
                reason_category=self.reason_category,
                comment=self.comment,
            )
        except ValueError as error:
            raise ReviewError(str(error)) from error
        object.__setattr__(self, "reason_category", reason)
        object.__setattr__(self, "comment", comment)


def _example_key(
    *,
    session: ReviewSession,
    finding: ReviewedFinding,
    regions: tuple[Rectangle, ...],
    confirmed_by: str,
    confirmed_at: datetime,
) -> str:
    """Отличает исправленные области без дублирования одинаковых подтверждений."""
    coordinates = tuple(
        (region.x_min, region.y_min, region.x_max, region.y_max) for region in regions
    )

    fingerprint = hashlib.sha256(
        repr((coordinates, confirmed_by, confirmed_at.isoformat())).encode("utf-8")
    ).hexdigest()[:16]

    return (
        f"{session.job_id}:{finding.finding_id}:"
        f"{finding.revision}:{finding.decision.value}:{fingerprint}"
    )


def _learning_use(finding: ReviewedFinding) -> LearningUse:
    """Не выдаёт исправленный и отклонённый текст за подтверждённую истину."""
    if finding.experience_tag == "edited" and finding.decision is Decision.REJECTED:
        return LearningUse.NEEDS_ADJUDICATION

    if finding.decision is Decision.REJECTED:
        return LearningUse.NEGATIVE

    return LearningUse.POSITIVE


def select_experience_candidates(
    *,
    session: ReviewSession,
    confirmed_areas: tuple[ConfirmedFindingArea, ...],
    for_catalog: bool = False,
) -> tuple[ExperienceCandidate, ...]:
    """Отбирает обучающие примеры либо все итоговые решения для каталога.

    Для отказа VLM crop относится к неизменной исходной области, а правки
    инженера сохраняются отдельно. Принятие подтверждает текущую область.
    Флаг каталога включает также текстовые решения и отклонённые Gold.
    """
    session.accepted_for_pdf()

    by_id: dict[str, ConfirmedFindingArea] = {}

    originals = {
        item.finding_id: item for item in session.findings if item.origin is Origin.VLM
    }

    for area in confirmed_areas:
        finding = originals.get(area.finding_id)

        if (
            area.job_id != session.job_id
            or finding is None
            or area.page_number != finding.page_number
            or area.page_number not in session.allowed_pages
            or area.confirmed_at < session.opened_at
            or area.finding_id in by_id
        ):
            raise ReviewError("Область не принадлежит текущей подтверждённой версии.")

        by_id[area.finding_id] = area

    candidates: list[ExperienceCandidate] = []

    for finding in session.findings:
        if finding.decision is Decision.PENDING:
            continue

        if finding.origin is Origin.MANUAL:
            if finding.decision is not Decision.ACCEPTED and not for_catalog:
                continue

            if (
                finding.issue_box is None
                or finding.callout_box is None
                or finding.page_number not in session.allowed_pages
            ):
                continue

            regions = (finding.issue_box,)
            callout_box = finding.callout_box

            confirmed_by = finding.updated_by
            confirmed_at = finding.updated_at
            area_source = "manual"

        else:
            area = by_id.get(finding.finding_id)
            if finding.decision is Decision.REJECTED and finding.proposed_regions:
                regions = tuple(item.bbox for item in finding.proposed_regions)
                callout_box = None
                confirmed_by, confirmed_at = "", finding.updated_at
                area_source = "vlm_original"
            elif area is None or finding.decision is Decision.REJECTED:
                if not for_catalog:
                    continue
                regions, callout_box = (), None
                confirmed_by, confirmed_at = "", finding.updated_at
                area_source = "unlocated"
            else:
                regions = area.regions
                callout_box = None
                confirmed_by = area.confirmed_by
                confirmed_at = area.confirmed_at
                area_source = "engineer_confirmed"

        tag = finding.experience_tag

        if tag is None:
            continue

        candidates.append(
            ExperienceCandidate(
                example_key=_example_key(
                    session=session,
                    finding=finding,
                    regions=regions,
                    confirmed_by=confirmed_by,
                    confirmed_at=confirmed_at,
                ),
                job_id=session.job_id,
                document_id=session.document_id,
                source_filename=session.source_filename,
                source_sha256=session.source_sha256,
                approved_revision=session.revision,
                finding_id=finding.finding_id,
                finding_revision=finding.revision,
                origin=finding.origin,
                tag=tag,
                decision=finding.decision,
                reason_category=finding.reason_category,
                comment=finding.comment,
                learning_use=_learning_use(finding),
                page_number=finding.page_number,
                issue_regions=regions,
                callout_box=callout_box,
                original_text=finding.original_text,
                text=finding.text,
                original_basis=finding.original_basis,
                normative_basis=finding.normative_basis,
                created_by=finding.created_by,
                updated_by=finding.updated_by,
                confirmed_by=confirmed_by,
                confirmed_at=confirmed_at,
                proposed_regions=finding.proposed_regions,
                display_regions=finding.display_regions,
                area_source=area_source,
            )
        )

    return tuple(candidates)
