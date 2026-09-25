# services/experience-service/tests/unit/test_review_persistence.py

"""Tests for Experience-owned tables, exact domain roundtrips and CAS SQL shape."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pdrd_experience_service.domain.review import (
    Decision,
    OriginalFinding,
    Rectangle,
    ReviewConflictError,
    ReviewSession,
)
from pdrd_experience_service.infrastructure.database.codec import (
    event_from_json,
    event_to_json,
    snapshot_from_json,
    snapshot_to_json,
)
from pdrd_experience_service.infrastructure.database.models import (
    Base,
    ReviewEventModel,
    ReviewSessionModel,
)
from pdrd_experience_service.infrastructure.database.repository import (
    SqlAlchemyReviewRepository,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

NOW = datetime(2026, 9, 25, tzinfo=UTC)

JOB = UUID(int=101)
DOC = UUID(int=102)


def session_with_full_history() -> ReviewSession:
    """Make one edited VLM and accepted manual Gold with full audit history."""
    session = ReviewSession.open(
        job_id=JOB,
        document_id=DOC,
        source_filename="drawing.pdf",
        source_sha256="a" * 64,
        originals=(
            OriginalFinding(
                "v1",
                2,
                "Исходная VLM",
                "СП 1",
            ),
        ),
        rendered_pages=(2,),
        actor="engineer:1",
        at=NOW,
    )

    session = session.edit(
        finding_id="v1",
        text="Исправлено",
        normative_basis="СП 2",
        actor="engineer:2",
        at=NOW,
        expected_revision=session.revision,
    )

    session = session.add_manual(
        finding_id=f"manual:{UUID(int=77)}",
        page_number=2,
        text="Пропуск VLM",
        normative_basis="СП 3",
        issue_box=Rectangle(
            100,
            110,
            200,
            210,
        ),
        callout_box=Rectangle(
            450,
            460,
            750,
            700,
        ),
        actor="engineer:3",
        at=NOW,
        expected_revision=session.revision,
    )

    for finding in session.findings:
        session = session.decide(
            finding_id=finding.finding_id,
            decision=Decision.ACCEPTED,
            actor="engineer:1",
            at=NOW,
            expected_revision=session.revision,
        )

    return session.approve(
        actor="engineer:1",
        at=NOW,
        expected_revision=session.revision,
    )


def test_snapshot_roundtrip_preserves_original_edit_gold_geometry_and_approval() -> (
    None
):
    """No original text, editor attribution or approved revision is lost in JSONB."""
    source = session_with_full_history()

    payload = snapshot_to_json(
        source,
    )

    assert "history" not in payload
    assert payload["findings"][0]["origin"] == "vlm"

    restored_events = tuple(
        event_from_json(
            action=event.action.value,
            actor=event.actor,
            occurred_at=event.occurred_at,
            revision=event.session_revision,
            details=event_to_json(event),
        )
        for event in source.history
    )

    restored = snapshot_from_json(
        payload,
        restored_events,
    )

    assert restored == source

    assert restored.accepted_for_pdf() == source.accepted_for_pdf()

    assert restored.findings[0].experience_tag == "edited"

    assert restored.findings[1].experience_tag == "gold"

    assert restored.findings[1].issue_box == Rectangle(100, 110, 200, 210)


def test_roundtrip_rejects_naive_audit_metadata() -> None:
    """Corrupt persisted timestamp is rejected rather than silently repaired."""
    source = session_with_full_history()

    data = snapshot_to_json(
        source,
    )

    data["opened_at"] = "2026-09-25T00:00:00"

    with pytest.raises(
        ValueError,
        match="timezone",
    ):
        snapshot_from_json(
            data,
            source.history,
        )


def test_orm_uses_separate_experience_schema_and_append_only_event_key() -> None:
    """Avoid changing Gateway/Knowledge version tables and overwriting history."""
    assert set(Base.metadata.tables) == {
        "experience.review_sessions",
        "experience.review_events",
    }

    assert [column.name for column in ReviewEventModel.__table__.primary_key] == [
        "job_id",
        "session_revision",
    ]

    sessions_sql = str(
        CreateTable(
            ReviewSessionModel.__table__,
        ).compile(
            dialect=postgresql.dialect(),
        )
    )

    events_sql = str(
        CreateTable(
            ReviewEventModel.__table__,
        ).compile(
            dialect=postgresql.dialect(),
        )
    )

    assert "experience.review_sessions" in sessions_sql

    assert "experience.review_events" in events_sql

    assert "JSONB" in sessions_sql and "JSONB" in events_sql

    assert "ck_review_sessions_approval" in sessions_sql


def test_repository_requires_one_audited_revision_per_update() -> None:
    """Reject snapshots whose audit trail could not be committed atomically."""
    import asyncio

    repo = SqlAlchemyReviewRepository(
        session_factory=lambda: None,  # type: ignore[arg-type]
    )

    source = session_with_full_history()

    with pytest.raises(
        ValueError,
        match="one new audited revision",
    ):
        asyncio.run(
            repo.update(
                source,
                expected_revision=0,
            )
        )


class FakeDatabase:
    """Capture SQL writes without pretending to emulate PostgreSQL transaction isolation."""

    def __init__(
        self,
        changed: UUID | None = JOB,
    ) -> None:
        """Select success or a stale CAS conflict."""
        self.changed = changed

        self.added: list[object] = []
        self.statement: object | None = None

    async def __aenter__(
        self,
    ) -> "FakeDatabase":
        """Open a fake session or transaction."""
        return self

    async def __aexit__(
        self,
        *args: object,
    ) -> None:
        """Finish a fake session or transaction."""

    def begin(
        self,
    ) -> "FakeDatabase":
        """Model the transaction boundary used by the adapter."""
        return self

    async def scalar(
        self,
        statement: object,
    ) -> UUID | None:
        """Capture the revision-guarded SQL UPDATE and its returned job."""
        self.statement = statement
        return self.changed

    def add(
        self,
        object_: object,
    ) -> None:
        """Track rows queued within the same transaction."""
        self.added.append(
            object_,
        )


@pytest.mark.asyncio
async def test_insert_queues_initial_snapshot_and_opened_event_together() -> None:
    """Insert must not silently lose the first audited revision."""
    initial = ReviewSession.open(
        job_id=JOB,
        document_id=DOC,
        source_filename="source.pdf",
        source_sha256="a" * 64,
        originals=(),
        rendered_pages=(1,),
        actor="engineer:1",
        at=NOW,
    )

    database = FakeDatabase()

    await SqlAlchemyReviewRepository(
        lambda: database,  # type: ignore[arg-type]
    ).insert(
        initial,
    )

    assert len(database.added) == 2

    assert isinstance(
        database.added[0],
        ReviewSessionModel,
    )

    assert isinstance(
        database.added[1],
        ReviewEventModel,
    )

    assert database.added[1].session_revision == 0


@pytest.mark.asyncio
async def test_update_guards_expected_revision_and_adds_only_new_audit_event() -> None:
    """Concurrency control is in the SQL WHERE clause, not in Python memory."""
    approved = session_with_full_history()

    database = FakeDatabase()

    await SqlAlchemyReviewRepository(
        lambda: database,  # type: ignore[arg-type]
    ).update(
        approved,
        expected_revision=approved.revision - 1,
    )

    assert len(database.added) == 1

    event = database.added[0]

    assert isinstance(
        event,
        ReviewEventModel,
    )

    assert event.action == "approved"

    compiled = database.statement.compile(
        dialect=postgresql.dialect(),
    )

    assert "review_sessions.revision =" in str(compiled)

    assert approved.revision - 1 in compiled.params.values()


@pytest.mark.asyncio
async def test_stale_database_update_fails_before_appending_an_event() -> None:
    """Two browser tabs cannot silently append conflicting review revisions."""
    approved = session_with_full_history()

    database = FakeDatabase(
        changed=None,
    )

    with pytest.raises(
        ReviewConflictError,
        match="Stale",
    ):
        await SqlAlchemyReviewRepository(
            lambda: database,  # type: ignore[arg-type]
        ).update(
            approved,
            expected_revision=approved.revision - 1,
        )

    assert database.added == []
