"""Создаёт локальные учётные данные внешних пользователей в схеме auth.

Revision ID: 20261001_auth_0002
Revises: 20260930_auth_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20261001_auth_0002"
down_revision: str | Sequence[str] | None = "20260930_auth_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Добавляет только таблицу внешнего входа собственного Auth Service."""
    op.create_table(
        "external_credentials",
        sa.Column("subject", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), unique=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("verification_token_hash", sa.String(64), unique=True),
        sa.Column("verification_expires_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "verification_token_hash IS NULL OR length(verification_token_hash) = 64",
            name="ck_external_credentials_token_hash_length",
        ),
        sa.CheckConstraint(
            "(verification_token_hash IS NULL) = (verification_expires_at IS NULL)",
            name="ck_external_credentials_verification_pair",
        ),
        schema="auth",
    )
    op.create_table(
        "rate_limits",
        sa.Column("key_hash", sa.String(64), primary_key=True),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.CheckConstraint("attempts >= 1", name="ck_rate_limits_attempts"),
        schema="auth",
    )


def downgrade() -> None:
    """Удаляет только собственную таблицу внешних учётных данных."""
    op.drop_table("rate_limits", schema="auth")
    op.drop_table("external_credentials", schema="auth")
