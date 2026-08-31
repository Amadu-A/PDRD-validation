# services/api-gateway/alembic/versions/20260831_0001_create_analysis_jobs.py

"""Создаёт таблицу асинхронных заданий анализа.

Revision ID: 20260831_0001
Revises:
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Создаёт analysis_jobs и необходимые индексы."""
    op.create_table(
        "analysis_jobs",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "error_code",
            sa.String(length=128),
            nullable=True,
        ),
        sa.Column(
            "error_message",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ("
            "'pending', "
            "'queued', "
            "'processing', "
            "'completed', "
            "'failed', "
            "'cancelled'"
            ")",
            name="ck_analysis_jobs_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_analysis_jobs_attempt_count",
        ),
        sa.PrimaryKeyConstraint(
            "id",
        ),
    )

    op.create_index(
        "ix_analysis_jobs_status",
        "analysis_jobs",
        ["status"],
        unique=False,
    )

    op.create_index(
        "ix_analysis_jobs_created_at",
        "analysis_jobs",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Удаляет analysis_jobs."""
    op.drop_index(
        "ix_analysis_jobs_created_at",
        table_name="analysis_jobs",
    )

    op.drop_index(
        "ix_analysis_jobs_status",
        table_name="analysis_jobs",
    )

    op.drop_table(
        "analysis_jobs",
    )
