# services/experience-service/src/pdrd_experience_service/infrastructure/database/models.py

"""Таблицы PostgreSQL, которыми владеет только Experience Service.

Снимки Review и их история остаются неизменными. Подтверждённая геометрия
имеет самостоятельную ревизию и собственную неизменяемую историю действий.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
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
    """Метаданные, принадлежащие цепочке миграций Experience Service."""


class ReviewSessionModel(Base):
    """Последний снимок каждого задания с номером утверждённой редакции."""

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
        {"schema": "experience"},
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
    """Неизменяемые действия над содержимым и решениями Human Review."""

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
        {"schema": "experience"},
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


class ConfirmedAreaModel(Base):
    """Последнее состояние инженерного подтверждения области одного VLM finding.

    Не удаляем запись при изменении текста: старая версия остаётся для аудита,
    но перестаёт проходить проверку content_signature при чтении.
    """

    __tablename__ = "confirmed_areas"

    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id"],
            ["experience.review_sessions.job_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "revision >= 1",
            name="ck_confirmed_areas_revision",
        ),
        {"schema": "experience"},
    )

    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
    )
    finding_id: Mapped[str] = mapped_column(
        String(256),
        primary_key=True,
    )
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    page_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    source_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    content_signature: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    review_revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    regions: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB,
        nullable=False,
    )
    mode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )
    note: Mapped[str] = mapped_column(
        String(1000),
        nullable=False,
    )
    confirmed_by: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
    )


class AreaConfirmationEventModel(Base):
    """Append-only журнал подтверждений, исправлений и отзывов координат."""

    __tablename__ = "area_confirmation_events"

    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id", "finding_id"],
            [
                "experience.confirmed_areas.job_id",
                "experience.confirmed_areas.finding_id",
            ],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "revision >= 1",
            name="ck_area_events_revision",
        ),
        {"schema": "experience"},
    )

    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
    )
    finding_id: Mapped[str] = mapped_column(
        String(256),
        primary_key=True,
    )
    revision: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )
    review_revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    action: Mapped[str] = mapped_column(
        String(16),
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
