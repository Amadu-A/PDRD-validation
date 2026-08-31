# services/api-gateway/src/pdrd_api_gateway/infrastructure/database/models.py

"""SQLAlchemy persistence models API Gateway."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
    text,
)
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
