# services/knowledge-service/alembic/versions/20261005_0005_package_owners.py

"""Добавляет владельца личных пакетов, сохраняя исторические записи скрытыми."""

import sqlalchemy as sa
from alembic import op

revision = "20261005_0005"
down_revision = "20260905_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Добавляет UUID владельца без чужого FK и индекс для личного каталога."""
    for table in ("normative_categories", "normative_documents"):
        op.add_column(
            table,
            sa.Column("owner_user_id", sa.Uuid(), nullable=True),
            schema="knowledge",
        )
        op.create_index(
            f"ix_{table}_owner_section",
            table,
            ["owner_user_id", "section_id"],
            schema="knowledge",
        )
        op.create_check_constraint(
            f"ck_{table}_normative_owner",
            table,
            "catalog_area = 'user_package' OR owner_user_id IS NULL",
            schema="knowledge",
        )


def downgrade() -> None:
    """Удаляет только добавленные ограничения, индекс и поле владельца."""
    for table in ("normative_categories", "normative_documents"):
        op.drop_constraint(
            f"ck_{table}_normative_owner", table, schema="knowledge", type_="check"
        )
        op.drop_index(f"ix_{table}_owner_section", table_name=table, schema="knowledge")
        op.drop_column(table, "owner_user_id", schema="knowledge")
