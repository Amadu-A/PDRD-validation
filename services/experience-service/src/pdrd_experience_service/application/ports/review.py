# services/experience-service/src/pdrd_experience_service/application/ports/review.py

"""Ports for authoritative analysis retrieval and atomic Human Review storage."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pdrd_experience_service.domain.review import (
    OriginalFinding,
    ReviewSession,
)


@dataclass(frozen=True, slots=True)
class CompletedAnalysis:
    """Server-verified completed analysis without unverified hypotheses."""

    job_id: UUID
    document_id: UUID
    source_filename: str
    source_sha256: str
    findings: tuple[OriginalFinding, ...]
    rendered_pages: tuple[int, ...]


class CompletedAnalysisReader(Protocol):
    """Read an authoritative completed analysis, not data supplied by a browser."""

    async def load(
        self,
        job_id: UUID,
    ) -> CompletedAnalysis:
        """Raise LookupError for unknown or not-completed jobs."""
        ...


class ReviewRepository(Protocol):
    """Persistent review snapshots with atomic revision compare-and-swap."""

    async def load(
        self,
        job_id: UUID,
    ) -> ReviewSession | None:
        """Return the latest stored session, or None if not yet created."""
        ...

    async def insert(
        self,
        session: ReviewSession,
    ) -> None:
        """Create session; fail atomically on concurrent duplicate insertion."""
        ...

    async def update(
        self,
        session: ReviewSession,
        *,
        expected_revision: int,
    ) -> None:
        """Commit session and audit in one transaction; stale revision raises conflict."""
        ...
