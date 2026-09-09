# services/api-gateway/alembic/versions/20260903_0003_add_normative_snapshot.py

"""Добавляет immutable normative snapshot analysis job.

Revision ID: 20260903_0003
Revises: 20260831_0002
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260903_0003"

down_revision: str | Sequence[str] | None = "20260831_0002"

branch_labels: str | Sequence[str] | None = None

depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Добавляет JSONB snapshot выбранных нормативов и active prompt."""
    op.add_column(
        "analysis_jobs",
        sa.Column(
            "normative_snapshot",
            postgresql.JSONB(),
            nullable=True,
        ),
    )

    op.create_check_constraint(
        "ck_analysis_jobs_normative_snapshot_object",
        "analysis_jobs",
        ("normative_snapshot IS NULL OR jsonb_typeof(normative_snapshot) = 'object'"),
    )


def downgrade() -> None:
    """Удаляет normative snapshot analysis job."""
    op.drop_constraint(
        "ck_analysis_jobs_normative_snapshot_object",
        "analysis_jobs",
        type_="check",
    )

    op.drop_column(
        "analysis_jobs",
        "normative_snapshot",
    )
