# services/experience-service/src/pdrd_experience_service/domain/area_confirmation.py

"""Правила явного подтверждения областей замечаний инженером.

Отделяет предложенные VLM-рамки от проверенной человеком геометрии.
Подпись связывает подтверждение с PDF, текстом и последней правкой,
но не меняется при простом принятии либо отклонении замечания.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pdrd_experience_service.domain.review import (
    Action,
    Origin,
    Rectangle,
    ReviewConflictError,
    ReviewedFinding,
    ReviewError,
    ReviewSession,
)


class ConfirmationMode(StrEnum):
    """Способ получения области, явно выбранный инженером."""

    PROPOSED = "proposed"
    REDRAWN = "redrawn"


@dataclass(frozen=True, slots=True)
class AreaConfirmation:
    """Команда подтверждения, привязанная к проверенной редакции Review."""

    job_id: UUID
    finding_id: str
    page_number: int
    regions: tuple[Rectangle, ...]
    source_sha256: str
    content_signature: str
    review_revision: int
    mode: ConfirmationMode
    note: str
    confirmed_by: str
    confirmed_at: datetime


@dataclass(frozen=True, slots=True)
class AreaConfirmationReceipt:
    """Результат сохранения либо отзыва с новой версией подтверждения."""

    job_id: UUID
    finding_id: str
    confirmation_revision: int
    active: bool


def find_vlm_finding(
    session: ReviewSession,
    finding_id: str,
) -> ReviewedFinding:
    """Проверяет, что ID принадлежит исходному VLM-замечанию этого задания."""
    finding = next(
        (item for item in session.findings if item.finding_id == finding_id),
        None,
    )
    if (
        finding is None
        or finding.origin is not Origin.VLM
        or finding.page_number not in session.allowed_pages
    ):
        raise ReviewError(
            "Подтверждать можно только исходное VLM-замечание на листе PDF."
        )
    return finding


def content_signature(
    session: ReviewSession,
    finding: ReviewedFinding,
) -> str:
    """Связывает область с источником и последней содержательной правкой.

    Решение ACCEPTED/REJECTED не влияет на подпись. Даже если инженер
    вернёт прежний текст или область, последняя ревизия EDITED/GEOMETRY
    изменится и старое подтверждение не будет использоваться повторно.
    """
    last_edit = max(
        (
            event.session_revision
            for event in session.history
            if event.action in (Action.EDITED, Action.GEOMETRY)
            and event.after is not None
            and event.after.finding_id == finding.finding_id
        ),
        default=0,
    )

    proposals = [
        {
            "box": [
                float(area.bbox.x_min),
                float(area.bbox.y_min),
                float(area.bbox.x_max),
                float(area.bbox.y_max),
            ],
            "source": area.source,
            "confidence": float(area.confidence),
            "method": area.method,
        }
        for area in finding.proposed_regions
    ]

    payload = {
        "job_id": str(session.job_id),
        "source_sha256": session.source_sha256,
        "finding_id": finding.finding_id,
        "page_number": finding.page_number,
        "original_text": finding.original_text,
        "original_basis": finding.original_basis,
        "text": finding.text,
        "normative_basis": finding.normative_basis,
        "last_edit_revision": last_edit,
        "proposals": proposals,
    }

    if finding.display_regions is not None:
        payload["display_regions"] = [
            [float(box.x_min), float(box.y_min), float(box.x_max), float(box.y_max)]
            for box in finding.display_regions
        ]

    serialized = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_confirmation(
    *,
    session: ReviewSession,
    finding_id: str,
    regions: tuple[Rectangle, ...],
    mode: ConfirmationMode,
    note: str,
    actor: str,
    at: datetime,
    expected_review_revision: int,
) -> AreaConfirmation:
    """Валидирует инженерное действие, не меняя Review и его решение."""
    if expected_review_revision != session.revision:
        raise ReviewConflictError("Ревизия Human Review устарела.")

    finding = find_vlm_finding(
        session,
        finding_id,
    )

    if (
        not isinstance(regions, tuple)
        or not 1 <= len(regions) <= 4
        or any(not isinstance(box, Rectangle) for box in regions)
        or len(set(regions)) != len(regions)
    ):
        raise ReviewError("Нужны от одной до четырёх неповторяющихся областей.")

    if any(
        isinstance(value, bool)
        for box in regions
        for value in (
            box.x_min,
            box.y_min,
            box.x_max,
            box.y_max,
        )
    ):
        raise ReviewError(
            "Координаты должны быть числами, а не логическими значениями."
        )

    if not isinstance(mode, ConfirmationMode):
        raise ReviewError("Укажите способ подтверждения области.")

    if mode is ConfirmationMode.PROPOSED:
        offered = {area.bbox for area in finding.proposed_regions}
        if not offered or any(box not in offered for box in regions):
            raise ReviewError(
                "Подтверждаемая VLM-область отсутствует в исходном Review."
            )

    if not isinstance(note, str) or len(note.strip()) > 1000:
        raise ReviewError(
            "Комментарий к подтверждению не должен превышать 1000 символов."
        )

    if mode is ConfirmationMode.REDRAWN and not note.strip():
        raise ReviewError("При исправлении координат укажите причину.")

    if not isinstance(actor, str) or not 1 <= len(actor.strip()) <= 128:
        raise ReviewError("Подтверждение требует идентификатор инженера от сервера.")

    if (
        not isinstance(at, datetime)
        or at.tzinfo is None
        or at.utcoffset() is None
        or at < session.opened_at
    ):
        raise ReviewError(
            "Дата подтверждения должна быть достоверной и с часовым поясом."
        )

    return AreaConfirmation(
        job_id=session.job_id,
        finding_id=finding.finding_id,
        page_number=finding.page_number,
        regions=regions,
        source_sha256=session.source_sha256,
        content_signature=content_signature(session, finding),
        review_revision=session.revision,
        mode=mode,
        note=note.strip(),
        confirmed_by=actor.strip(),
        confirmed_at=at.astimezone(UTC),
    )
