# services/api-gateway/alembic/versions/20261006_0006_analysis_history.py

"""Добавляет составной индекс истории без изменения заданий и их владельцев."""

from alembic import op

revision = "20261006_0006"
down_revision = "20261001_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Ускоряет упорядоченную выборку собственных проверок пользователя."""
    op.create_index(
        "ix_analysis_jobs_owner_history",
        "analysis_jobs",
        ["owner_user_id", "created_at", "id"],
    )


def downgrade() -> None:
    """Удаляет только индекс, сохраняя результаты и принадлежность заданий."""
    op.drop_index("ix_analysis_jobs_owner_history", table_name="analysis_jobs")
