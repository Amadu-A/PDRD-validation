# services/api-gateway/alembic/versions/20261006_0007_analysis_retention.py

"""Отмечает очистку исходников без изменения владельцев, Review и результатов."""

import sqlalchemy as sa
from alembic import op

revision = "20261006_0007"
down_revision = "20261006_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Добавляет отметку для повторяемой пакетной очистки старых проверок."""
    op.add_column(
        "analysis_jobs",
        sa.Column(
            "source_artifacts_deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Удаляет отметку; ранее удалённые по политике файлы не восстанавливает."""
    op.drop_column("analysis_jobs", "source_artifacts_deleted_at")
