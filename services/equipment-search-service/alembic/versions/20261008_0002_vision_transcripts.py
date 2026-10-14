# services/equipment-search-service/alembic/versions/20261008_0002_vision_transcripts.py

"""Сохранённые визуальные строки для воспроизводимого EQ Facts.

Revision ID: 20261008_0002
Revises: 20261008_0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261008_0002"
down_revision = "20261008_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Добавляет транскрипцию отдельно от неизменяемых байтов документа."""
    op.add_column(
        "documents",
        sa.Column("vision_facts", JSONB(), nullable=False, server_default="[]"),
        schema="equipment_search",
    )


def downgrade() -> None:
    """Удаляет транскрипции только по явной команде оператора."""
    op.drop_column("documents", "vision_facts", schema="equipment_search")
