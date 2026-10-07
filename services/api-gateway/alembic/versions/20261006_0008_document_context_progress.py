# services/api-gateway/alembic/versions/20261006_0008_document_context_progress.py

"""Разрешает durable progress этапов контекста PDF и межстраничной проверки."""

from alembic import op

revision = "20261006_0008"
down_revision = "20261006_0007"
branch_labels = None
depends_on = None

_OLD = (
    "progress_stage IS NULL OR progress_stage IN ("
    "'extracting_sources', 'preparing_context', 'understanding_sheet', "
    "'retrieving_requirements', 'checking_requirements', "
    "'enriching_findings', 'finalizing_findings', 'building_result')"
)
_NEW = (
    "progress_stage IS NULL OR progress_stage IN ("
    "'extracting_sources', 'preparing_context', 'understanding_sheet', "
    "'building_document_context', 'retrieving_requirements', "
    "'checking_requirements', 'checking_cross_page_consistency', "
    "'enriching_findings', 'finalizing_findings', 'building_result')"
)


def upgrade() -> None:
    """Допускает два дополнительных монотонных этапа анализа PDF."""
    op.drop_constraint(
        "ck_analysis_jobs_progress_stage", "analysis_jobs", type_="check"
    )
    op.create_check_constraint("ck_analysis_jobs_progress_stage", "analysis_jobs", _NEW)


def downgrade() -> None:
    """Возвращает прежние допустимые этапы без потери заданий."""
    op.execute(
        "UPDATE analysis_jobs SET progress_stage = 'understanding_sheet' "
        "WHERE progress_stage = 'building_document_context'"
    )
    op.execute(
        "UPDATE analysis_jobs SET progress_stage = 'checking_requirements' "
        "WHERE progress_stage = 'checking_cross_page_consistency'"
    )
    op.drop_constraint(
        "ck_analysis_jobs_progress_stage", "analysis_jobs", type_="check"
    )
    op.create_check_constraint("ck_analysis_jobs_progress_stage", "analysis_jobs", _OLD)
