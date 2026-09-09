# services/knowledge-service/alembic/versions/20260902_0002_create_normative_outbox.py

"""Создаёт transactional outbox нормативной индексации.

Revision ID: 20260902_0002
Revises: 20260902_0001
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260902_0002"
down_revision: str | Sequence[str] | None = "20260902_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_NAME = "knowledge"


def upgrade() -> None:
    """Создаёт таблицу durable outbox."""
    op.create_table(
        "normative_outbox_messages",
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
            sa.String(
                length=128,
            ),
            nullable=False,
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(
                astext_type=sa.Text(),
            ),
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text(
                "0",
            ),
            nullable=False,
        ),
        sa.Column(
            "last_error",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(
                timezone=True,
            ),
            server_default=sa.text(
                "CURRENT_TIMESTAMP",
            ),
            nullable=False,
        ),
        sa.Column(
            "published_at",
            sa.DateTime(
                timezone=True,
            ),
            nullable=True,
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_normative_outbox_attempt_count",
        ),
        sa.ForeignKeyConstraint(
            [
                "aggregate_id",
            ],
            [
                f"{SCHEMA_NAME}.normative_documents.id",
            ],
            name="fk_normative_outbox_document",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_normative_outbox_messages",
        ),
        schema=SCHEMA_NAME,
    )

    op.create_index(
        "ix_normative_outbox_pending",
        "normative_outbox_messages",
        [
            "published_at",
            "created_at",
        ],
        unique=False,
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    """Удаляет transactional outbox."""
    op.drop_index(
        "ix_normative_outbox_pending",
        table_name="normative_outbox_messages",
        schema=SCHEMA_NAME,
    )

    op.drop_table(
        "normative_outbox_messages",
        schema=SCHEMA_NAME,
    )
