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


class ExternalCredentialModel(Base):
    """Собственная таблица внешних паролей и хешей одноразовых кодов."""

    __tablename__ = "external_credentials"
    __table_args__ = (
        CheckConstraint(
            "verification_token_hash IS NULL OR length(verification_token_hash) = 64",
            name="ck_external_credentials_token_hash_length",
        ),
        CheckConstraint(
            "(verification_token_hash IS NULL) = (verification_expires_at IS NULL)",
            name="ck_external_credentials_verification_pair",
        ),
        {"schema": "auth"},
    )

    subject: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), unique=True, nullable=True
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    verification_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class LocalCredentialModel(Base):
    """Хеш локального пароля; незавершённый bootstrap не допускает вход."""

    __tablename__ = "local_credentials"
    __table_args__ = (
        CheckConstraint(
            "username = lower(username) AND length(username) BETWEEN 1 AND 64",
            name="ck_local_credentials_username",
        ),
        {"schema": "auth"},
    )

    subject: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), unique=True)


class RateLimitModel(Base):
    """Общий для процессов счётчик попыток без открытого IP или логина."""

    __tablename__ = "rate_limits"
    __table_args__ = (
        CheckConstraint("attempts >= 1", name="ck_rate_limits_attempts"),
        {"schema": "auth"},
    )

    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
