# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/database/technical_assignment_models.py

"""SQLAlchemy models технических заданий."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
)

from pdrd_knowledge_service.infrastructure.database.base import (
    KNOWLEDGE_SCHEMA,
    Base,
)


class TechnicalAssignmentModel(
    Base,
):
    """ORM lifecycle одного project-specific ТЗ."""

    __tablename__ = "technical_assignments"

    __table_args__ = (
        CheckConstraint(
            "size_bytes > 0",
            name="ck_technical_assignments_size_positive",
        ),
        CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_technical_assignments_sha256",
        ),
        CheckConstraint(
            "index_status IN ("
            "'uploaded', "
            "'queued', "
            "'indexing', "
            "'ready', "
            "'failed', "
            "'deleting'"
            ")",
            name="ck_technical_assignments_index_status",
        ),
        UniqueConstraint(
            "analysis_document_id",
            name="uq_technical_assignments_analysis_document",
        ),
        Index(
            "ix_technical_assignments_section_id",
            "section_id",
        ),
        Index(
            "ix_technical_assignments_index_status",
            "index_status",
        ),
        {
            "schema": KNOWLEDGE_SCHEMA,
        },
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(
            as_uuid=True,
        ),
        primary_key=True,
    )

    analysis_document_id: Mapped[UUID] = mapped_column(
        Uuid(
            as_uuid=True,
        ),
        nullable=False,
    )

    section_id: Mapped[UUID] = mapped_column(
        Uuid(
            as_uuid=True,
        ),
        ForeignKey(
            f"{KNOWLEDGE_SCHEMA}.normative_sections.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    original_name: Mapped[str] = mapped_column(
        String(
            255,
        ),
        nullable=False,
    )

    mime_type: Mapped[str] = mapped_column(
        String(
            255,
        ),
        nullable=False,
    )

    size_bytes: Mapped[int] = mapped_column(
        BigInteger(),
        nullable=False,
    )

    sha256: Mapped[str] = mapped_column(
        String(
            64,
        ),
        nullable=False,
    )

    index_status: Mapped[str] = mapped_column(
        String(
            32,
        ),
        nullable=False,
    )

    index_error: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )

    indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(
            timezone=True,
        ),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(
            timezone=True,
        ),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(
            timezone=True,
        ),
        nullable=False,
    )


class TechnicalAssignmentOutboxMessageModel(
    Base,
):
    """ORM durable outbox ТЗ."""

    __tablename__ = "technical_assignment_outbox_messages"

    __table_args__ = (
        CheckConstraint(
            "attempt_count >= 0",
            name=("ck_technical_assignment_outbox_attempt_count"),
        ),
        Index(
            "ix_technical_assignment_outbox_pending",
            "published_at",
            "created_at",
        ),
        {
            "schema": KNOWLEDGE_SCHEMA,
        },
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(
            as_uuid=True,
        ),
        primary_key=True,
    )

    aggregate_id: Mapped[UUID] = mapped_column(
        Uuid(
            as_uuid=True,
        ),
        ForeignKey(
            (f"{KNOWLEDGE_SCHEMA}.technical_assignments.id"),
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    event_type: Mapped[str] = mapped_column(
        String(
            128,
        ),
        nullable=False,
    )

    payload: Mapped[dict[str, str]] = mapped_column(
        JSONB,
        nullable=False,
    )

    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text(
            "0",
        ),
    )

    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(
            timezone=True,
        ),
        nullable=False,
    )

    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(
            timezone=True,
        ),
        nullable=True,
    )
