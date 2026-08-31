# services/api-gateway/alembic/versions/20260831_0002_add_analysis_outbox.py

"""Добавляет document_id и transactional outbox.

Revision ID: 20260831_0002
Revises: 20260831_0001
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260831_0002"
down_revision: str | Sequence[str] | None = "20260831_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Добавляет связь job с документом и outbox table."""
    op.add_column(
        "analysis_jobs",
        sa.Column(
            "document_id",
            sa.Uuid(),
            nullable=True,
        ),
    )

    op.create_table(
        "outbox_messages",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column(
            "aggregate_id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column(
            "event_type",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "last_error",
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
            "published_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_outbox_messages_attempt_count",
        ),
        sa.ForeignKeyConstraint(
            ["aggregate_id"],
            ["analysis_jobs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
        ),
    )

    op.create_index(
        "ix_outbox_messages_pending",
        "outbox_messages",
        [
            "published_at",
            "created_at",
        ],
        unique=False,
    )


def downgrade() -> None:
    """Удаляет transactional outbox и document_id."""
    op.drop_index(
        "ix_outbox_messages_pending",
        table_name="outbox_messages",
    )

    op.drop_table(
        "outbox_messages",
    )

    op.drop_column(
        "analysis_jobs",
        "document_id",
    )
