# services/auth-service/alembic/versions/20260930_0001_create_sessions.py

"""Создаёт собственную таблицу серверных сессий auth-service.

Revision ID: 20260930_auth_0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20260930_auth_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Создаёт только схему auth и таблицу хешей сессий."""
    op.execute("CREATE SCHEMA IF NOT EXISTS auth")
    op.create_table(
        "sessions",
        sa.Column("session_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("authorization_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
        sa.CheckConstraint(
            "length(token_hash) = 64", name="ck_sessions_token_hash_length"
        ),
        sa.CheckConstraint(
            "authorization_version >= 1", name="ck_sessions_authorization_version"
        ),
        sa.CheckConstraint(
            "created_at <= last_seen_at AND last_seen_at < idle_expires_at "
            "AND idle_expires_at <= absolute_expires_at",
            name="ck_sessions_chronology",
        ),
        schema="auth",
    )
    op.create_index(
        "ix_sessions_user_created", "sessions", ["user_id", "created_at"], schema="auth"
    )


def downgrade() -> None:
    """Удаляет только принадлежащую auth-service таблицу."""
    op.drop_index("ix_sessions_user_created", table_name="sessions", schema="auth")
    op.drop_table("sessions", schema="auth")
