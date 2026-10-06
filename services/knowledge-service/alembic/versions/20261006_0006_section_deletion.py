# services/knowledge-service/alembic/versions/20261006_0006_section_deletion.py

"""Сохраняет состояние повторяемого удаления нормативного раздела."""

import sqlalchemy as sa
from alembic import op

revision = "20261006_0006"
down_revision = "20261005_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Добавляет отметку, запрещающую новые загрузки после начала удаления."""
    op.add_column(
        "normative_sections",
        sa.Column(
            "deleting", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        schema="knowledge",
    )


def downgrade() -> None:
    """Удаляет отметку жизненного цикла раздела."""
    op.drop_column("normative_sections", "deleting", schema="knowledge")
