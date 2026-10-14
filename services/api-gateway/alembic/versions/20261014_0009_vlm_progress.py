# services/api-gateway/alembic/versions/20261014_0009_vlm_progress.py

"""Добавляет отдельный этап T-first и счётчики очереди VLM в progress задания."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261014_0009"
down_revision = "20261006_0008"
branch_labels = None
depends_on = None

_OLD = (
    "progress_stage IS NULL OR progress_stage IN ("
    "'extracting_sources', 'preparing_context', 'understanding_sheet', "
    "'building_document_context', 'retrieving_requirements', "
    "'checking_requirements', 'checking_cross_page_consistency', "
    "'enriching_findings', 'finalizing_findings', 'building_result')"
)
_NEW = _OLD.replace(
    "'retrieving_requirements'",
    "'checking_technical_assignment', 'retrieving_requirements'",
)


def upgrade() -> None:
    """Расширяет progress без изменения сохранённых заданий и результатов."""
    op.add_column("analysis_jobs", sa.Column("progress_gpu", JSONB, nullable=True))
    op.drop_constraint(
        "ck_analysis_jobs_progress_stage", "analysis_jobs", type_="check"
    )
    op.create_check_constraint("ck_analysis_jobs_progress_stage", "analysis_jobs", _NEW)


def downgrade() -> None:
    """Возвращает прежнюю схему, сопоставляя T-first с предыдущим этапом."""
    op.execute(
        "UPDATE analysis_jobs SET progress_stage = 'building_document_context' "
        "WHERE progress_stage = 'checking_technical_assignment'"
    )
    op.drop_constraint(
        "ck_analysis_jobs_progress_stage", "analysis_jobs", type_="check"
    )
    op.create_check_constraint("ck_analysis_jobs_progress_stage", "analysis_jobs", _OLD)
    op.drop_column("analysis_jobs", "progress_gpu")
