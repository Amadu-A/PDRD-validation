# services/knowledge-service/alembic/versions/20261008_0008_equipment_facts.py

"""Кэш структурированных EQ Facts по неизменяемому документу."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261008_0008"
down_revision = "20261006_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Создаёт отдельное от N/T/U/D/E хранилище характеристик производителя."""
    op.create_table(
        "equipment_fact_sets",
        sa.Column("document_source_id", sa.String(80), primary_key=True),
        sa.Column("document_sha256", sa.String(64), nullable=False),
        sa.Column("manufacturer", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("variant", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), primary_key=True),
        sa.Column("extractor_version", sa.Integer(), primary_key=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("facts", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="knowledge",
    )


def downgrade() -> None:
    """Удаляет только EQ Facts, не затрагивая нормативные индексы."""
    op.drop_table("equipment_fact_sets", schema="knowledge")
