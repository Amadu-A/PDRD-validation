# services/experience-service/src/pdrd_experience_service/domain/review.py

"""Immutable Human Review domain; no HTTP, SQLAlchemy, VLM or PDF dependencies."""

import math
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID


class ReviewError(ValueError):
    """Invalid review command, identity or geometry."""


class ReviewConflictError(ReviewError):
    """Review revision does not match the caller's revision."""


class ReviewNotReadyError(ReviewError):
    """Human review is incomplete or has not been approved."""


class Origin(StrEnum):
    """Source of the original finding."""

    VLM = "vlm"
    MANUAL = "manual"


class Decision(StrEnum):
    """Explicit human review decision, separate from finding provenance."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class Action(StrEnum):
    """Audited review operation."""

    OPENED = "opened"
    ADDED = "added"
    EDITED = "edited"
    DECIDED = "decided"
    APPROVED = "approved"


def _text(value: str, *, limit: int = 10000, required: bool = True) -> str:
    """Validate human-authored text without silently truncating it."""
    result = value.strip() if isinstance(value, str) else ""
    if (required and not result) or len(result) > limit:
        raise ReviewError(
            f"Expected text length 1..{limit}."
            if required
            else f"Text exceeds {limit}."
        )
    return result


def _actor(value: str) -> str:
    """Require a trusted actor supplied by the server-side identity adapter."""
    return _text(value, limit=128)


def _time(value: datetime) -> datetime:
    """Store only offset-aware timestamps in UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ReviewError("Audit time must be timezone-aware.")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class Rectangle:
    """Normalized rectangle of a single PDF page, 0..1000 in both axes."""

    x_min: float
    y_min: float
    x_max: float
    y_max: float

    def __post_init__(self) -> None:
        """Reject invalid or degenerate user-drawn regions."""
        values = (self.x_min, self.y_min, self.x_max, self.y_max)
        if (
            not all(math.isfinite(value) for value in values)
            or not (0 <= self.x_min < self.x_max <= 1000)
            or not (0 <= self.y_min < self.y_max <= 1000)
        ):
            raise ReviewError("Rectangle must lie inside one page (0..1000).")


@dataclass(frozen=True, slots=True)
class ProposedRegion:
    """Автоматическая область визуализации, ожидающая проверки инженером."""

    bbox: Rectangle
    source: str
    confidence: float
    method: str

    def __post_init__(self) -> None:
        """Не допускает пустую provenance или некорректную уверенность."""
        if (
            not isinstance(self.bbox, Rectangle)
            or not isinstance(self.source, str)
            or not self.source.strip()
            or not isinstance(self.method, str)
            or not self.method.strip()
            or not isinstance(self.confidence, (int, float))
            or isinstance(self.confidence, bool)
            or not math.isfinite(self.confidence)
            or not 0 <= self.confidence <= 1
        ):
            raise ReviewError("Некорректная предложенная область визуализации.")


@dataclass(frozen=True, slots=True)
class OriginalFinding:
    """Authoritative, non-hypothesis finding from the completed analysis."""

    finding_id: str
    page_number: int
    text: str
    normative_basis: str = ""
    proposed_regions: tuple[ProposedRegion, ...] = ()

    def __post_init__(self) -> None:
        """Validate original source data before opening a review."""
        _text(self.finding_id, limit=256)

        if self.finding_id != self.finding_id.strip():
            raise ReviewError("VLM finding IDs must be canonical.")

        if self.finding_id.startswith("manual:") or self.page_number < 1:
            raise ReviewError("Invalid VLM finding identity or physical page.")

        _text(self.text)
        _text(self.normative_basis, limit=2000, required=False)

        if not isinstance(self.proposed_regions, tuple) or any(
            not isinstance(region, ProposedRegion) for region in self.proposed_regions
        ):
            raise ReviewError("Области VLM должны быть валидными объектами.")


@dataclass(frozen=True, slots=True)
class ReviewedFinding:
    """Current version; immutable original content is retained for training."""

    finding_id: str
    origin: Origin
    page_number: int
    original_text: str
    text: str
    original_basis: str
    normative_basis: str
    decision: Decision
    issue_box: Rectangle | None
    callout_box: Rectangle | None
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime
    revision: int = 0
    proposed_regions: tuple[ProposedRegion, ...] = ()

    @property
    def experience_tag(self) -> str | None:
        """Represent the finding's source and correction, not its decision."""
        if self.origin is Origin.MANUAL:
            return "gold"

        if (
            self.text != self.original_text
            or self.normative_basis != self.original_basis
        ):
            return "edited"

        if self.decision is Decision.ACCEPTED:
            return "wise"

        if self.decision is Decision.REJECTED:
            return "bad"

        return None


@dataclass(frozen=True, slots=True)
class ReviewEvent:
    """Immutable change history including before/after content and actor."""

    action: Action
    actor: str
    occurred_at: datetime
    session_revision: int
    before: ReviewedFinding | None = None
    after: ReviewedFinding | None = None


@dataclass(frozen=True, slots=True)
class ApprovedReview:
    """Exact accepted revision to pass to the future PDF rendering adapter."""

    job_id: UUID
    revision: int
    findings: tuple[ReviewedFinding, ...]


@dataclass(frozen=True, slots=True)
class ReviewSession:
    """One review per completed job; returns new states for optimistic CAS persistence."""

    job_id: UUID
    document_id: UUID
    source_filename: str
    source_sha256: str
    opened_by: str
    opened_at: datetime
    allowed_pages: tuple[int, ...]
    findings: tuple[ReviewedFinding, ...]
    history: tuple[ReviewEvent, ...]
    revision: int = 0
    approved_revision: int | None = None

    @classmethod
    def open(
        cls,
        *,
        job_id: UUID,
        document_id: UUID,
        source_filename: str,
        source_sha256: str,
        originals: tuple[OriginalFinding, ...],
        rendered_pages: tuple[int, ...],
        actor: str,
        at: datetime,
    ) -> "ReviewSession":
        """Initialize every authoritative VLM finding as pending, even if unlocated."""
        actor = _actor(actor)
        at = _time(at)
        source_filename = _text(source_filename, limit=512)

        if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
            raise ReviewError(
                "Source content SHA256 must be authoritative hexadecimal."
            )

        if len(set(rendered_pages)) != len(rendered_pages) or any(
            not isinstance(page, int) or isinstance(page, bool) or page < 1
            for page in rendered_pages
        ):
            raise ReviewError(
                "Rendered PDF page numbers must be distinct positive integers."
            )

        ids = [item.finding_id for item in originals]

        if len(ids) != len(set(ids)):
            raise ReviewError("Duplicate authoritative VLM finding IDs.")

        if any(
            item.proposed_regions and item.page_number not in rendered_pages
            for item in originals
        ):
            raise ReviewError("Область VLM привязана к неотрендеренному листу.")

        findings = tuple(
            ReviewedFinding(
                finding_id=item.finding_id,
                origin=Origin.VLM,
                page_number=item.page_number,
                original_text=item.text,
                text=item.text,
                original_basis=item.normative_basis,
                normative_basis=item.normative_basis,
                decision=Decision.PENDING,
                issue_box=None,
                callout_box=None,
                created_by=actor,
                created_at=at,
                updated_by=actor,
                updated_at=at,
                proposed_regions=item.proposed_regions,
            )
            for item in originals
        )

        return cls(
            job_id=job_id,
            document_id=document_id,
            source_filename=source_filename,
            source_sha256=source_sha256,
            opened_by=actor,
            opened_at=at,
            allowed_pages=tuple(rendered_pages),
            findings=findings,
            history=(ReviewEvent(Action.OPENED, actor, at, 0),),
        )

    @property
    def pending_count(self) -> int:
        """Include all authoritative VLM findings and all user-added Gold findings."""
        return sum(item.decision is Decision.PENDING for item in self.findings)

    def _expect(self, revision: int) -> None:
        """Reject stale writes before modifying the immutable state."""
        if revision != self.revision:
            raise ReviewConflictError(
                f"Stale review revision {revision}; current revision {self.revision}."
            )

    def _item(self, finding_id: str) -> ReviewedFinding:
        """Find an existing review entry without accepting client-invented VLM IDs."""
        item = next(
            (item for item in self.findings if item.finding_id == finding_id),
            None,
        )

        if item is None:
            raise ReviewError(f"Unknown review finding: {finding_id}.")

        return item

    def _update(
        self,
        *,
        before: ReviewedFinding,
        after: ReviewedFinding,
        action: Action,
        actor: str,
        at: datetime,
    ) -> "ReviewSession":
        """Replace one record and append an immutable audit event."""
        next_revision = self.revision + 1

        return replace(
            self,
            findings=tuple(
                after if item.finding_id == before.finding_id else item
                for item in self.findings
            ),
            history=(
                *self.history,
                ReviewEvent(
                    action,
                    actor,
                    at,
                    next_revision,
                    before,
                    after,
                ),
            ),
            revision=next_revision,
            approved_revision=None,
        )

    def add_manual(
        self,
        *,
        finding_id: str,
        page_number: int,
        text: str,
        normative_basis: str,
        issue_box: Rectangle,
        callout_box: Rectangle,
        actor: str,
        at: datetime,
        expected_revision: int,
    ) -> "ReviewSession":
        """Add Gold only on a source-verified rendered PDF page."""
        self._expect(expected_revision)
        actor = _actor(actor)
        at = _time(at)

        if not finding_id.startswith("manual:"):
            raise ReviewError("Manual ID requires the manual: prefix.")

        try:
            UUID(finding_id.removeprefix("manual:"))
        except ValueError as error:
            raise ReviewError("Manual finding ID must contain a UUID.") from error

        if any(item.finding_id == finding_id for item in self.findings):
            raise ReviewError("Duplicate review finding ID.")

        if (
            not isinstance(page_number, int)
            or isinstance(page_number, bool)
            or page_number not in self.allowed_pages
        ):
            raise ReviewError("Manual finding must belong to a rendered source page.")

        if not isinstance(issue_box, Rectangle) or not isinstance(
            callout_box, Rectangle
        ):
            raise ReviewError("Manual annotation requires two validated rectangles.")

        text = _text(text)
        normative_basis = _text(
            normative_basis,
            limit=2000,
            required=False,
        )

        record = ReviewedFinding(
            finding_id=finding_id,
            origin=Origin.MANUAL,
            page_number=page_number,
            original_text=text,
            text=text,
            original_basis=normative_basis,
            normative_basis=normative_basis,
            decision=Decision.PENDING,
            issue_box=issue_box,
            callout_box=callout_box,
            created_by=actor,
            created_at=at,
            updated_by=actor,
            updated_at=at,
        )

        new_revision = self.revision + 1

        return replace(
            self,
            findings=(*self.findings, record),
            history=(
                *self.history,
                ReviewEvent(
                    Action.ADDED,
                    actor,
                    at,
                    new_revision,
                    after=record,
                ),
            ),
            revision=new_revision,
            approved_revision=None,
        )

    def edit(
        self,
        *,
        finding_id: str,
        text: str,
        normative_basis: str,
        actor: str,
        at: datetime,
        expected_revision: int,
    ) -> "ReviewSession":
        """Change text/basis and invalidate any earlier approval."""
        self._expect(expected_revision)
        actor = _actor(actor)
        at = _time(at)
        record = self._item(finding_id)

        text = _text(text)
        normative_basis = _text(
            normative_basis,
            limit=2000,
            required=False,
        )

        if record.text == text and record.normative_basis == normative_basis:
            return self

        updated = replace(
            record,
            text=text,
            normative_basis=normative_basis,
            decision=Decision.PENDING,
            updated_by=actor,
            updated_at=at,
            revision=record.revision + 1,
        )

        return self._update(
            before=record,
            after=updated,
            action=Action.EDITED,
            actor=actor,
            at=at,
        )

    def decide(
        self,
        *,
        finding_id: str,
        decision: Decision,
        actor: str,
        at: datetime,
        expected_revision: int,
    ) -> "ReviewSession":
        """Apply an explicit accept/reject to one finding only."""
        self._expect(expected_revision)
        actor = _actor(actor)
        at = _time(at)

        if decision not in (
            Decision.ACCEPTED,
            Decision.REJECTED,
        ):
            raise ReviewError("A human decision must be accepted or rejected.")

        record = self._item(finding_id)

        if record.decision == decision:
            return self

        updated = replace(
            record,
            decision=decision,
            updated_by=actor,
            updated_at=at,
            revision=record.revision + 1,
        )

        return self._update(
            before=record,
            after=updated,
            action=Action.DECIDED,
            actor=actor,
            at=at,
        )

    def approve(
        self,
        *,
        actor: str,
        at: datetime,
        expected_revision: int,
    ) -> "ReviewSession":
        """Seal one reviewed revision only after every finding receives a decision."""
        self._expect(expected_revision)
        actor = _actor(actor)
        at = _time(at)

        if self.pending_count:
            raise ReviewNotReadyError(
                f"{self.pending_count} findings still require review."
            )

        if self.approved_revision == self.revision:
            return self

        next_revision = self.revision + 1

        return replace(
            self,
            revision=next_revision,
            approved_revision=next_revision,
            history=(
                *self.history,
                ReviewEvent(
                    Action.APPROVED,
                    actor,
                    at,
                    next_revision,
                ),
            ),
        )

    def accepted_for_pdf(self) -> ApprovedReview:
        """Return only current accepted entries; rejected entries remain in audit/Experience."""
        if self.approved_revision != self.revision or self.pending_count:
            raise ReviewNotReadyError(
                "Current review revision must be fully approved before PDF export."
            )

        return ApprovedReview(
            job_id=self.job_id,
            revision=self.revision,
            findings=tuple(
                item for item in self.findings if item.decision is Decision.ACCEPTED
            ),
        )
