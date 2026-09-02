# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/database/outbox_model.py

"""SQLAlchemy model transactional outbox нормативной индексации."""

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
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
)

from pdrd_knowledge_service.infrastructure.database.base import (
    KNOWLEDGE_SCHEMA,
    Base,
)


class NormativeOutboxMessageModel(Base):
    """ORM-представление durable outbox сообщения."""

    __tablename__ = "normative_outbox_messages"

    __table_args__ = (
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_normative_outbox_attempt_count",
        ),
        Index(
            "ix_normative_outbox_pending",
            "published_at",
            "created_at",
        ),
        {
            "schema": KNOWLEDGE_SCHEMA,
        },
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )

    aggregate_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            f"{KNOWLEDGE_SCHEMA}.normative_documents.id",
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
        server_default=text(
            "0",
        ),
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
