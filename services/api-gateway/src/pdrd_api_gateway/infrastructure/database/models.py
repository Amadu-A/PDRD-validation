# services/api-gateway/src/pdrd_api_gateway/infrastructure/database/models.py

"""SQLAlchemy persistence models API Gateway."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pdrd_api_gateway.infrastructure.database.base import Base


class AnalysisJobModel(Base):
    """ORM-представление таблицы заданий анализа."""

    __tablename__ = "analysis_jobs"

    __table_args__ = (
        CheckConstraint(
            "status IN ("
            "'pending', "
            "'queued', "
            "'processing', "
            "'completed', "
            "'failed', "
            "'cancelled'"
            ")",
            name="ck_analysis_jobs_status",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_analysis_jobs_attempt_count",
        ),
        Index(
            "ix_analysis_jobs_status",
            "status",
        ),
        Index(
            "ix_analysis_jobs_created_at",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )

    document_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'pending'"),
    )

    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )

    error_code: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class OutboxMessageModel(Base):
    """ORM-представление transactional outbox."""

    __tablename__ = "outbox_messages"

    __table_args__ = (
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_outbox_messages_attempt_count",
        ),
        Index(
            "ix_outbox_messages_pending",
            "published_at",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )

    aggregate_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "analysis_jobs.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    event_type: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    payload: Mapped[dict[str, str]] = mapped_column(
        JSONB,
        nullable=False,
    )

    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )

    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
