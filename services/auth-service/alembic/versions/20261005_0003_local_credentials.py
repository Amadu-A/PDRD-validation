# services/auth-service/alembic/versions/20261005_0003_local_credentials.py

"""Добавляет локальные пароли без изменения корпоративных идентичностей."""

import sqlalchemy as sa
from alembic import op

revision = "20261005_auth_0003"
down_revision = "20261001_auth_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Создаёт изолированное хранилище паролей локальных аккаунтов."""
    op.create_table(
        "local_credentials",
        sa.Column("subject", sa.Uuid(), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.Uuid(), unique=True),
        sa.CheckConstraint(
            "username = lower(username) AND length(username) BETWEEN 1 AND 64",
            name="ck_local_credentials_username",
        ),
        schema="auth",
    )


def downgrade() -> None:
    """Удаляет локальные пароли при явном откате миграции."""
    op.drop_table("local_credentials", schema="auth")
