# services/user-service/alembic/versions/20261006_0004_catalog_distribution.py

"""Фиксирует одноразовую выдачу новых разделов без изменения версий сессий."""

import sqlalchemy as sa
from alembic import op

revision = "20261006_0004"
down_revision = "20261005_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Добавляет marker распределения, не связывая таблицы разных сервисов."""
    op.create_table(
        "catalog_section_distributions",
        sa.Column("section_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "actor_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.accounts.user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="users",
    )


def downgrade() -> None:
    """Удаляет markers; downgrade допускает повторную выдачу уже созданных разделов."""
    op.drop_table("catalog_section_distributions", schema="users")
