# services/auth-service/src/pdrd_auth_service/infrastructure/database/models.py

"""Таблица серверных сессий без сырого браузерного токена и пароля AD."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from pdrd_auth_service.infrastructure.database.base import Base


class SessionModel(Base):
    """Содержит только SHA-256 токена и метаданные отзыва/истечения."""

    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint(
            "length(token_hash) = 64", name="ck_sessions_token_hash_length"
        ),
        CheckConstraint(
            "authorization_version >= 1", name="ck_sessions_authorization_version"
        ),
        CheckConstraint(
            "created_at <= last_seen_at AND last_seen_at < idle_expires_at "
            "AND idle_expires_at <= absolute_expires_at",
            name="ck_sessions_chronology",
        ),
        Index("ix_sessions_user_created", "user_id", "created_at"),
        {"schema": "auth"},
    )

    session_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    authorization_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    idle_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    absolute_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
