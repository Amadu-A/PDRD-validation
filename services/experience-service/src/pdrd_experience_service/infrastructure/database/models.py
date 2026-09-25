# services/experience-service/src/pdrd_experience_service/infrastructure/database/models.py

"""Experience-owned PostgreSQL tables; no cross-service ORM foreign keys."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Metadata shared only by Experience Service migrations."""


class ReviewSessionModel(Base):
    """Latest immutable-by-revision review snapshot for one completed job."""

    __tablename__ = "review_sessions"

    __table_args__ = (
        CheckConstraint(
            "revision >= 0",
            name="ck_review_sessions_revision",
        ),
        CheckConstraint(
            "approved_revision IS NULL OR approved_revision = revision",
            name="ck_review_sessions_approval",
        ),
        {
            "schema": "experience",
        },
    )

    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
    )

    document_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )

    source_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    approved_revision: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    snapshot: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class ReviewEventModel(Base):
    """Append-only audited commands, persisted atomically with each snapshot."""

    __tablename__ = "review_events"

    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id"],
            ["experience.review_sessions.job_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "session_revision >= 0",
            name="ck_review_events_revision",
        ),
        {
            "schema": "experience",
        },
    )

    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
    )

    session_revision: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    action: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    actor: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    details: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
    )
