# services/experience-service/tests/unit/test_review_use_cases.py

"""Application tests use fake ports; no DB, HTTP or PDF runtime needed."""

from uuid import UUID, uuid4

import pytest
from pdrd_experience_service.application.ports.review import (
    CompletedAnalysis,
)
from pdrd_experience_service.application.use_cases.review import (
    ChangeReview,
    OpenReview,
)
from pdrd_experience_service.domain.review import (
    Decision,
    OriginalFinding,
    Rectangle,
    ReviewConflictError,
    ReviewNotReadyError,
    ReviewSession,
)

JOB = UUID(int=1)
DOC = UUID(int=2)


class FakeAnalyses:
    """Simulate server-owned completed job and its authoritative findings."""

    def __init__(self) -> None:
        """Track source lookups."""
        self.calls = 0

    async def load(
        self,
        job_id: UUID,
    ) -> CompletedAnalysis:
        """Return one verified source and visible page."""
        self.calls += 1

        return CompletedAnalysis(
            job_id=job_id,
            document_id=DOC,
            source_filename="drawing.pdf",
            source_sha256="a" * 64,
            findings=(
                OriginalFinding(
                    "vlm:1",
                    2,
                    "Текст VLM",
                    "СП 1",
                ),
            ),
            rendered_pages=(2,),
        )


class FakeRepository:
    """Enforce an in-memory atomic revision guard for use-case tests."""

    def __init__(self) -> None:
        """Keep isolated single-job state."""
        self.session: ReviewSession | None = None
        self.updates = 0

    async def load(
        self,
        job_id: UUID,
    ) -> ReviewSession | None:
        """Return latest state."""
        return self.session if self.session and self.session.job_id == job_id else None

    async def insert(
        self,
        session: ReviewSession,
    ) -> None:
        """Reject second insert instead of overwriting first source snapshot."""
        if self.session is not None:
            raise ReviewConflictError("Concurrent insertion")

        self.session = session

    async def update(
        self,
        session: ReviewSession,
        *,
        expected_revision: int,
    ) -> None:
        """Compare-and-swap entire reviewed session and audit events."""
        if self.session is None or self.session.revision != expected_revision:
            raise ReviewConflictError("Concurrent revision")

        self.session = session
        self.updates += 1


@pytest.mark.asyncio
async def test_open_uses_authoritative_source_once_and_cannot_overwrite() -> None:
    """Reopening preserves previously reviewed decisions and original source."""
    source = FakeAnalyses()
    repo = FakeRepository()
    opener = OpenReview(source, repo)

    first = await opener.execute(
        job_id=JOB,
        actor="authenticated:1",
    )

    await ChangeReview(repo).decide(
        job_id=JOB,
        finding_id="vlm:1",
        decision=Decision.ACCEPTED,
        actor="authenticated:1",
        expected_revision=first.revision,
    )

    second = await opener.execute(
        job_id=JOB,
        actor="authenticated:2",
    )

    assert source.calls == 1
    assert second.findings[0].decision is Decision.ACCEPTED


@pytest.mark.asyncio
async def test_full_review_commands_produce_approved_snapshot_for_pdf() -> None:
    """Approved revision includes accepted Gold and edited VLM, not rejected rows."""
    repo = FakeRepository()

    await OpenReview(
        FakeAnalyses(),
        repo,
    ).execute(
        job_id=JOB,
        actor="authenticated:1",
    )

    commands = ChangeReview(repo)

    with pytest.raises(ReviewNotReadyError):
        await commands.approved_snapshot(
            job_id=JOB,
        )

    updated = await commands.edit(
        job_id=JOB,
        finding_id="vlm:1",
        text="Уточнённая формулировка",
        normative_basis="СП 7",
        actor="authenticated:1",
        expected_revision=0,
    )

    manual_id = f"manual:{uuid4()}"

    updated = await commands.add_manual(
        job_id=JOB,
        finding_id=manual_id,
        page_number=2,
        text="Пропуск VLM",
        normative_basis="СП 9",
        issue_box=Rectangle(
            100,
            120,
            200,
            240,
        ),
        callout_box=Rectangle(
            700,
            300,
            900,
            450,
        ),
        actor="authenticated:2",
        expected_revision=updated.revision,
    )

    updated = await commands.decide(
        job_id=JOB,
        finding_id="vlm:1",
        decision=Decision.REJECTED,
        actor="authenticated:2",
        expected_revision=updated.revision,
    )

    with pytest.raises(ReviewNotReadyError):
        await commands.approve(
            job_id=JOB,
            actor="authenticated:1",
            expected_revision=3,
        )

    updated = await commands.decide(
        job_id=JOB,
        finding_id=manual_id,
        decision=Decision.ACCEPTED,
        actor="authenticated:1",
        expected_revision=updated.revision,
    )

    sealed = await commands.approve(
        job_id=JOB,
        actor="authenticated:1",
        expected_revision=updated.revision,
    )

    export = await commands.approved_snapshot(
        job_id=JOB,
    )

    assert export.revision == sealed.revision
    assert len(export.findings) == 1
    assert export.findings[0].finding_id == manual_id
    assert export.findings[0].experience_tag == "gold"
    assert repo.updates == 5


@pytest.mark.asyncio
async def test_stale_client_revision_is_rejected_without_persistence() -> None:
    """No lost updates when browser A and browser B edit one report."""
    repo = FakeRepository()

    await OpenReview(
        FakeAnalyses(),
        repo,
    ).execute(
        job_id=JOB,
        actor="authenticated:1",
    )

    commands = ChangeReview(repo)

    await commands.decide(
        job_id=JOB,
        finding_id="vlm:1",
        decision=Decision.ACCEPTED,
        actor="authenticated:1",
        expected_revision=0,
    )

    with pytest.raises(ReviewConflictError):
        await commands.decide(
            job_id=JOB,
            finding_id="vlm:1",
            decision=Decision.REJECTED,
            actor="authenticated:2",
            expected_revision=0,
        )

    assert repo.updates == 1


@pytest.mark.asyncio
async def test_no_implicit_session_creation_on_write() -> None:
    """Only explicit source-verified open command initializes the session."""
    with pytest.raises(LookupError):
        await ChangeReview(
            FakeRepository(),
        ).approved_snapshot(
            job_id=JOB,
        )
