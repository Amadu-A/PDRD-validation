# services/experience-service/src/pdrd_experience_service/infrastructure/database/codec.py

"""Explicit, JSON-safe encoding of immutable Review domain states and audit events."""

from dataclasses import asdict
from datetime import datetime
from typing import Any
from uuid import UUID

from pdrd_experience_service.domain.review import (
    Action,
    Decision,
    Origin,
    ProposedRegion,
    Rectangle,
    ReviewedFinding,
    ReviewEvent,
    ReviewSession,
)


def _encode(value: Any) -> Any:
    """Normalize UUID, timestamps and nested dataclass dicts before JSONB storage."""
    if isinstance(value, (UUID, datetime)):
        return str(value) if isinstance(value, UUID) else value.isoformat()

    if isinstance(value, dict):
        return {key: _encode(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]

    return value


def _timestamp(value: str) -> datetime:
    """Recover a timezone-aware timestamp; reject corrupt persisted audit metadata."""
    result = datetime.fromisoformat(value)

    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Stored review timestamps must include a timezone.")

    return result


def _rectangle(
    value: dict[str, Any] | None,
) -> Rectangle | None:
    """Restore and validate a normalized PDF rectangle."""
    return Rectangle(**value) if value is not None else None


def _proposed_regions(value: list[dict[str, Any]]) -> tuple[ProposedRegion, ...]:
    """Восстанавливает кандидаты областей без повышения их до подтверждений."""
    return tuple(
        ProposedRegion(
            bbox=Rectangle(**item["bbox"]),
            source=item["source"],
            confidence=item["confidence"],
            method=item["method"],
        )
        for item in value
    )


def finding_to_json(
    finding: ReviewedFinding,
) -> dict[str, Any]:
    """Encode current/audited review findings without losing provenance."""
    return _encode(
        asdict(finding),
    )


def finding_from_json(
    value: dict[str, Any],
) -> ReviewedFinding:
    """Restore a typed finding, including both Gold rectangles when present."""
    return ReviewedFinding(
        finding_id=value["finding_id"],
        origin=Origin(value["origin"]),
        page_number=value["page_number"],
        original_text=value["original_text"],
        text=value["text"],
        original_basis=value["original_basis"],
        normative_basis=value["normative_basis"],
        decision=Decision(value["decision"]),
        issue_box=_rectangle(value["issue_box"]),
        callout_box=_rectangle(value["callout_box"]),
        created_by=value["created_by"],
        created_at=_timestamp(value["created_at"]),
        updated_by=value["updated_by"],
        updated_at=_timestamp(value["updated_at"]),
        revision=value["revision"],
        proposed_regions=_proposed_regions(value.get("proposed_regions", [])),
    )


def snapshot_to_json(
    session: ReviewSession,
) -> dict[str, Any]:
    """Serialize current state only; event history remains in its own SQL table."""
    return {
        "job_id": str(session.job_id),
        "document_id": str(session.document_id),
        "source_filename": session.source_filename,
        "source_sha256": session.source_sha256,
        "opened_by": session.opened_by,
        "opened_at": session.opened_at.isoformat(),
        "allowed_pages": list(session.allowed_pages),
        "findings": [finding_to_json(finding) for finding in session.findings],
        "revision": session.revision,
        "approved_revision": session.approved_revision,
    }


def snapshot_from_json(
    snapshot: dict[str, Any],
    events: tuple[ReviewEvent, ...],
) -> ReviewSession:
    """Reconstruct an operational review with its separate, complete audit trail."""
    return ReviewSession(
        job_id=UUID(snapshot["job_id"]),
        document_id=UUID(snapshot["document_id"]),
        source_filename=snapshot["source_filename"],
        source_sha256=snapshot["source_sha256"],
        opened_by=snapshot["opened_by"],
        opened_at=_timestamp(snapshot["opened_at"]),
        allowed_pages=tuple(snapshot["allowed_pages"]),
        findings=tuple(finding_from_json(row) for row in snapshot["findings"]),
        history=events,
        revision=snapshot["revision"],
        approved_revision=snapshot["approved_revision"],
    )


def event_to_json(
    event: ReviewEvent,
) -> dict[str, Any]:
    """Encode full before/after audit evidence for a separate immutable row."""
    return {
        "before": (finding_to_json(event.before) if event.before else None),
        "after": (finding_to_json(event.after) if event.after else None),
    }


def event_from_json(
    *,
    action: str,
    actor: str,
    occurred_at: datetime,
    revision: int,
    details: dict[str, Any],
) -> ReviewEvent:
    """Decode an audited command and preserve historical original/edited texts."""
    return ReviewEvent(
        action=Action(action),
        actor=actor,
        occurred_at=occurred_at,
        session_revision=revision,
        before=(finding_from_json(details["before"]) if details["before"] else None),
        after=(finding_from_json(details["after"]) if details["after"] else None),
    )
