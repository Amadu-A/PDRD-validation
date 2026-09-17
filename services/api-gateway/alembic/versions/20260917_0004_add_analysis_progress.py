# services/api-gateway/alembic/versions/20260917_0004_add_analysis_progress.py

"""Добавляет durable progress stage analysis job.

Revision ID: 20260917_0004
Revises: 20260903_0003
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260917_0004"

down_revision: str | Sequence[str] | None = "20260903_0003"

branch_labels: str | Sequence[str] | None = None

depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Добавляет текущий устойчивый этап выполнения анализа."""
    op.add_column(
        "analysis_jobs",
        sa.Column(
            "progress_stage",
            sa.String(
                length=64,
            ),
            nullable=True,
        ),
    )

    op.create_check_constraint(
        "ck_analysis_jobs_progress_stage",
        "analysis_jobs",
        (
            "progress_stage IS NULL OR progress_stage IN ("
            "'extracting_sources', "
            "'preparing_context', "
            "'understanding_sheet', "
            "'retrieving_requirements', "
            "'checking_requirements', "
            "'enriching_findings', "
            "'finalizing_findings', "
            "'building_result'"
            ")"
        ),
    )


def downgrade() -> None:
    """Удаляет durable progress stage."""
    op.drop_constraint(
        "ck_analysis_jobs_progress_stage",
        "analysis_jobs",
        type_="check",
    )

    op.drop_column(
        "analysis_jobs",
        "progress_stage",
    )
