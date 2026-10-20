# services/knowledge-service/alembic/versions/20261020_0009_normative_requirements.py

"""Добавляет поколения атомарных нормативов и журнал доказательств без переиндексации корпуса."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261020_0009"
down_revision = "20261008_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Создаёт пустые таблицы; рабочие чанки и документы не изменяются."""
    op.create_table(
        "normative_requirement_generations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("knowledge.normative_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.CheckConstraint(
            "state IN ('queued','building','ready','failed')",
            name="ck_requirement_generation_state",
        ),
        sa.CheckConstraint(
            "NOT active OR state = 'ready'",
            name="ck_requirement_generation_active_ready",
        ),
        schema="knowledge",
    )
    op.create_index(
        "ix_knowledge_normative_requirement_generations_document_id",
        "normative_requirement_generations",
        ["document_id"],
        schema="knowledge",
    )
    op.create_index(
        "uq_requirement_active_generation",
        "normative_requirement_generations",
        ["document_id"],
        unique=True,
        schema="knowledge",
        postgresql_where=sa.text("active"),
    )
    op.create_table(
        "normative_requirements",
        sa.Column(
            "generation_id",
            sa.Uuid(),
            sa.ForeignKey(
                "knowledge.normative_requirement_generations.id", ondelete="CASCADE"
            ),
            primary_key=True,
        ),
        sa.Column("requirement_id", sa.String(48), primary_key=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.UniqueConstraint(
            "generation_id", "ordinal", name="uq_normative_requirement_ordinal"
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_normative_requirement_ordinal"),
        schema="knowledge",
    )
    op.create_table(
        "normative_status_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("knowledge.normative_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        schema="knowledge",
    )
    op.create_index(
        "ix_knowledge_normative_status_evidence_document_id",
        "normative_status_evidence",
        ["document_id"],
        schema="knowledge",
    )


def downgrade() -> None:
    """Удаляет только данные этапа A; исходные нормативы и векторные чанки сохраняются."""
    op.drop_table("normative_status_evidence", schema="knowledge")
    op.drop_table("normative_requirements", schema="knowledge")
    op.drop_table("normative_requirement_generations", schema="knowledge")
