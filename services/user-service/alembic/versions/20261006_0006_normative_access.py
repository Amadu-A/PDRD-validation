# services/user-service/alembic/versions/20261006_0006_normative_access.py

"""Добавляет отдельное назначение удаления нормативных объектов с аудитом; по умолчанию доступ закрыт."""

import sqlalchemy as sa
from alembic import op

revision = "20261006_0006"
down_revision = "20261006_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Сохраняет ручной флаг отдельно от автоматических прав рабочих ролей."""
    op.add_column(
        "accounts",
        sa.Column(
            "normative_access_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        schema="users",
    )
    op.create_table(
        "normative_access_events",
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.accounts.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.accounts.user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("authorization_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "authorization_version >= 2", name="ck_normative_access_version"
        ),
        schema="users",
    )


def downgrade() -> None:
    """Удаляет назначение и аудит при явном откате миграции."""
    op.drop_table("normative_access_events", schema="users")
    op.drop_column("accounts", "normative_access_enabled", schema="users")
