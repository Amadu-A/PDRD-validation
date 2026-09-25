# services/experience-service/src/pdrd_experience_service/infrastructure/database/repository.py

"""Atomic PostgreSQL ReviewRepository with optimistic revision compare-and-swap."""

from collections.abc import Callable
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pdrd_experience_service.domain.review import (
    ReviewConflictError,
    ReviewError,
    ReviewEvent,
    ReviewSession,
)
from pdrd_experience_service.infrastructure.database.codec import (
    event_from_json,
    event_to_json,
    snapshot_from_json,
    snapshot_to_json,
)
from pdrd_experience_service.infrastructure.database.models import (
    ReviewEventModel,
    ReviewSessionModel,
)


class SqlAlchemyReviewRepository:
    """One transaction per write, with immutable event and snapshot atomicity."""

    def __init__(
        self,
        session_factory: Callable[[], AsyncSession],
    ) -> None:
        """Inject session factory rather than opening global database connections."""
        self._session_factory = session_factory

    async def load(
        self,
        job_id: UUID,
    ) -> ReviewSession | None:
        """Reconstruct the latest persisted snapshot and every associated audit event."""
        async with self._session_factory() as database:
            row = await database.get(
                ReviewSessionModel,
                job_id,
            )

            if row is None:
                return None

            results = await database.scalars(
                select(ReviewEventModel)
                .where(
                    ReviewEventModel.job_id == job_id,
                )
                .order_by(
                    ReviewEventModel.session_revision,
                )
            )

            history = tuple(
                event_from_json(
                    action=event.action,
                    actor=event.actor,
                    occurred_at=event.occurred_at,
                    revision=event.session_revision,
                    details=event.details,
                )
                for event in results.all()
            )

            revisions = tuple(event.session_revision for event in history)

            if revisions != tuple(range(row.revision + 1)):
                raise ReviewError(
                    "Incomplete or inconsistent persisted review history."
                )

            snapshot = snapshot_from_json(
                row.snapshot,
                history,
            )

            if snapshot.job_id != job_id or snapshot.revision != row.revision:
                raise ReviewError(
                    "Persisted review snapshot identity/revision mismatch."
                )

            return snapshot

    async def insert(
        self,
        session: ReviewSession,
    ) -> None:
        """Create a new review and its opened event, rejecting simultaneous opens."""
        if session.revision != 0 or len(session.history) != 1:
            raise ReviewError("Initial review must have exactly one opened event.")

        event = session.history[0]

        if event.session_revision != 0 or event.action.value != "opened":
            raise ReviewError("Initial review must start with its opened event.")

        try:
            async with (
                self._session_factory() as database,
                database.begin(),
            ):
                database.add(
                    ReviewSessionModel(
                        job_id=session.job_id,
                        document_id=session.document_id,
                        source_sha256=session.source_sha256,
                        revision=session.revision,
                        approved_revision=session.approved_revision,
                        snapshot=snapshot_to_json(session),
                        updated_at=session.opened_at,
                    )
                )

                database.add(
                    self._event_row(
                        session.job_id,
                        event,
                    )
                )

        except IntegrityError as error:
            raise ReviewConflictError("Review for this job already exists.") from error

    async def update(
        self,
        session: ReviewSession,
        *,
        expected_revision: int,
    ) -> None:
        """CAS current revision and append exactly one event in one DB transaction."""
        new_events = tuple(
            event
            for event in session.history
            if event.session_revision > expected_revision
        )

        if (
            session.revision != expected_revision + 1
            or len(new_events) != 1
            or new_events[0].session_revision != session.revision
        ):
            raise ReviewError("Review update must contain one new audited revision.")

        event = new_events[0]

        statement = (
            update(ReviewSessionModel)
            .where(
                ReviewSessionModel.job_id == session.job_id,
                ReviewSessionModel.revision == expected_revision,
            )
            .values(
                revision=session.revision,
                approved_revision=session.approved_revision,
                snapshot=snapshot_to_json(session),
                updated_at=event.occurred_at,
            )
            .returning(
                ReviewSessionModel.job_id,
            )
        )

        try:
            async with (
                self._session_factory() as database,
                database.begin(),
            ):
                changed = await database.scalar(
                    statement,
                )

                if changed is None:
                    raise ReviewConflictError("Stale or missing review revision.")

                database.add(
                    self._event_row(
                        session.job_id,
                        event,
                    )
                )

        except IntegrityError as error:
            raise ReviewConflictError("Concurrent review event collision.") from error

    @staticmethod
    def _event_row(
        job_id: UUID,
        event: ReviewEvent,
    ) -> ReviewEventModel:
        """Build immutable audit row without exposing ORM to the application layer."""
        return ReviewEventModel(
            job_id=job_id,
            session_revision=event.session_revision,
            action=event.action.value,
            actor=event.actor,
            occurred_at=event.occurred_at,
            details=event_to_json(event),
        )
