# services/knowledge-service/alembic/versions/20260905_0004_create_technical_assignments.py

"""Создаёт lifecycle и outbox технических заданий.

Revision ID: 20260905_0004
Revises: 20260903_0003
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260905_0004"
down_revision: str | Sequence[str] | None = "20260903_0003"

branch_labels: str | Sequence[str] | None = None

depends_on: str | Sequence[str] | None = None

SCHEMA_NAME = "knowledge"


def upgrade() -> None:
    """Создаёт T lifecycle и отдельный durable outbox."""
    op.create_table(
        "technical_assignments",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column(
            "analysis_document_id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column(
            "section_id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column(
            "original_name",
            sa.String(
                length=255,
            ),
            nullable=False,
        ),
        sa.Column(
            "mime_type",
            sa.String(
                length=255,
            ),
            nullable=False,
        ),
        sa.Column(
            "size_bytes",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "sha256",
            sa.String(
                length=64,
            ),
            nullable=False,
        ),
        sa.Column(
            "index_status",
            sa.String(
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "index_error",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "indexed_at",
            sa.DateTime(
                timezone=True,
            ),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(
                timezone=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(
                timezone=True,
            ),
            nullable=False,
        ),
        sa.CheckConstraint(
            "size_bytes > 0",
            name="ck_technical_assignments_size_positive",
        ),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_technical_assignments_sha256",
        ),
        sa.CheckConstraint(
            "index_status IN ("
            "'uploaded', "
            "'queued', "
            "'indexing', "
            "'ready', "
            "'failed', "
            "'deleting'"
            ")",
            name="ck_technical_assignments_index_status",
        ),
        sa.CheckConstraint(
            "("
            "index_status = 'failed' "
            "AND index_error IS NOT NULL "
            "AND btrim(index_error) <> ''"
            ") OR ("
            "index_status <> 'failed' "
            "AND index_error IS NULL"
            ")",
            name="ck_technical_assignments_index_error",
        ),
        sa.CheckConstraint(
            "("
            "index_status = 'ready' "
            "AND indexed_at IS NOT NULL"
            ") OR ("
            "index_status <> 'ready' "
            "AND indexed_at IS NULL"
            ")",
            name="ck_technical_assignments_indexed_at",
        ),
        sa.ForeignKeyConstraint(
            [
                "section_id",
            ],
            [
                f"{SCHEMA_NAME}.normative_sections.id",
            ],
            name="fk_technical_assignments_section",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_technical_assignments",
        ),
        sa.UniqueConstraint(
            "analysis_document_id",
            name="uq_technical_assignments_analysis_document",
        ),
        schema=SCHEMA_NAME,
    )

    op.create_index(
        "ix_technical_assignments_section_id",
        "technical_assignments",
        [
            "section_id",
        ],
        unique=False,
        schema=SCHEMA_NAME,
    )

    op.create_index(
        "ix_technical_assignments_index_status",
        "technical_assignments",
        [
            "index_status",
        ],
        unique=False,
        schema=SCHEMA_NAME,
    )

    op.create_table(
        "technical_assignment_outbox_messages",
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
            name="ck_technical_assignment_outbox_attempt_count",
        ),
        sa.ForeignKeyConstraint(
            [
                "aggregate_id",
            ],
            [
                f"{SCHEMA_NAME}.technical_assignments.id",
            ],
            name="fk_technical_assignment_outbox_assignment",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_technical_assignment_outbox_messages",
        ),
        schema=SCHEMA_NAME,
    )

    op.create_index(
        "ix_technical_assignment_outbox_pending",
        "technical_assignment_outbox_messages",
        [
            "published_at",
            "created_at",
        ],
        unique=False,
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    """Удаляет только сущности ТЗ."""
    op.drop_index(
        "ix_technical_assignment_outbox_pending",
        table_name=("technical_assignment_outbox_messages"),
        schema=SCHEMA_NAME,
    )

    op.drop_table(
        "technical_assignment_outbox_messages",
        schema=SCHEMA_NAME,
    )

    op.drop_index(
        "ix_technical_assignments_index_status",
        table_name="technical_assignments",
        schema=SCHEMA_NAME,
    )

    op.drop_index(
        "ix_technical_assignments_section_id",
        table_name="technical_assignments",
        schema=SCHEMA_NAME,
    )

    op.drop_table(
        "technical_assignments",
        schema=SCHEMA_NAME,
    )
