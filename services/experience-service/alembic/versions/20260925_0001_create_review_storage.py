# services/experience-service/alembic/versions/20260925_0001_create_review_storage.py

"""Create dedicated Experience schema, review snapshots and immutable audit trail.

Revision ID: 20260925_0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20260925_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add Experience-owned tables without mutating the Gateway/Knowledge schemas."""
    op.execute("CREATE SCHEMA IF NOT EXISTS experience")

    op.create_table(
        "review_sessions",
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "document_id",
            UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "source_sha256",
            sa.String(64),
            nullable=False,
        ),
        sa.Column(
            "revision",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "approved_revision",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "snapshot",
            JSONB(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "revision >= 0",
            name="ck_review_sessions_revision",
        ),
        sa.CheckConstraint(
            "approved_revision IS NULL OR approved_revision = revision",
            name="ck_review_sessions_approval",
        ),
        schema="experience",
    )

    op.create_table(
        "review_events",
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "session_revision",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "action",
            sa.String(32),
            nullable=False,
        ),
        sa.Column(
            "actor",
            sa.String(128),
            nullable=False,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "details",
            JSONB(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "job_id",
            "session_revision",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["experience.review_sessions.job_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "session_revision >= 0",
            name="ck_review_events_revision",
        ),
        schema="experience",
    )


def downgrade() -> None:
    """Reverse only this migration; never call in ordinary production deploy."""
    op.drop_table(
        "review_events",
        schema="experience",
    )

    op.drop_table(
        "review_sessions",
        schema="experience",
    )

    # Deliberately leave the schema to avoid deleting unrelated future tables.
