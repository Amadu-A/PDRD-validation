# services/knowledge-service/alembic/versions/20261006_0007_source_retention.py

"""Добавляет отметку удаления исходного ТЗ без удаления его бессрочных требований."""

import sqlalchemy as sa
from alembic import op

revision = "20261006_0007"
down_revision = "20261006_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Позволяет отличить окончание срока от временного сбоя чтения файла."""
    op.add_column(
        "technical_assignments",
        sa.Column(
            "source_removed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        schema="knowledge",
    )


def downgrade() -> None:
    """Удаляет только отметку; удалённые физические файлы не восстанавливает."""
    op.drop_column("technical_assignments", "source_removed_at", schema="knowledge")
