# services/equipment-search-service/alembic/versions/20261012_0004_durable_search_progress.py

"""Сохраняет порционный прогресс, политику и версии длительной EQ-проверки."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20261012_0004"
down_revision = "20261009_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Добавляет checkpoint без изменения сохранённых документов и чужих схем."""
    op.add_column(
        "documents",
        sa.Column("article", sa.Text(), nullable=False, server_default=""),
        schema="equipment_search",
    )
    for name in ("policy", "checkpoint"):
        op.add_column(
            "equipment_jobs",
            sa.Column(name, JSONB(), nullable=False, server_default="{}"),
            schema="equipment_search",
        )
    op.add_column(
        "equipment_jobs",
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
        schema="equipment_search",
    )
    op.add_column(
        "equipment_jobs",
        sa.Column("resume_count", sa.Integer(), nullable=False, server_default="0"),
        schema="equipment_search",
    )

    op.create_table(
        "job_versions",
        sa.Column("job_id", UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("job_id", "revision"),
        sa.ForeignKeyConstraint(
            ["job_id"], ["equipment_search.equipment_jobs.job_id"], ondelete="CASCADE"
        ),
        schema="equipment_search",
    )
    op.create_table(
        "job_models",
        sa.Column("job_id", UUID(as_uuid=True), nullable=False),
        sa.Column("model_index", sa.Integer(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("job_id", "model_index"),
        sa.CheckConstraint(
            "model_index >= 0 AND model_index < 500", name="ck_equipment_model_index"
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["equipment_search.equipment_jobs.job_id"], ondelete="CASCADE"
        ),
        schema="equipment_search",
    )


def downgrade() -> None:
    """Удаляет служебный прогресс при явном откате оператора."""
    op.drop_table("job_versions", schema="equipment_search")
    op.drop_table("job_models", schema="equipment_search")
    op.drop_column("documents", "article", schema="equipment_search")
    for name in ("resume_count", "revision", "checkpoint", "policy"):
        op.drop_column("equipment_jobs", name, schema="equipment_search")
