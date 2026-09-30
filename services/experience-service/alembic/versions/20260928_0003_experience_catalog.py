# services/experience-service/alembic/versions/20260928_0003_experience_catalog.py

"""Постоянный каталог Experience и неизменяемая история его редакций.

Revision ID: 20260928_0003
Revises: 20260928_0002

Создаёт только собственные таблицы. Исходные Review/подтверждения и общие
сервисы не меняются. PNG принадлежат отдельному volume experience_crops.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20260928_0003"
down_revision = "20260928_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Сначала каталог, затем зависимый журнал; прежние данные сохраняются."""
    op.create_table(
        "catalog_examples",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("example_key", sa.Text(), nullable=False, unique=True),
        sa.Column("job_id", UUID(as_uuid=True), nullable=False),
        sa.Column("finding_id", sa.String(256), nullable=False),
        sa.Column("approved_revision", sa.Integer(), nullable=False),
        sa.Column("area_revision", sa.Integer(), nullable=False),
        sa.Column("area_signature", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("tag", sa.String(16), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("learning_use", sa.String(32), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("document_title", sa.String(500), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("normative_basis", sa.Text(), nullable=False),
        sa.Column("source_filename", sa.Text(), nullable=False),
        sa.Column("snapshot", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision >= 0", name="ck_catalog_revision"),
        schema="experience",
    )
    for column in ("job_id", "tag", "created_at"):
        op.create_index(
            f"ix_experience_catalog_examples_{column}",
            "catalog_examples",
            [column],
            schema="experience",
        )
    op.create_table(
        "catalog_events",
        sa.Column("example_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot", JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["example_id"], ["experience.catalog_examples.id"], ondelete="CASCADE"
        ),
        schema="experience",
    )


def downgrade() -> None:
    """Удаляет только собственный каталог; требует отдельного решения об утрате примеров."""
    op.drop_table("catalog_events", schema="experience")
    op.drop_table("catalog_examples", schema="experience")
