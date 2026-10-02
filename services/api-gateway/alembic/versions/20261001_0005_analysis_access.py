"""Сохраняет владельца анализа и срок действия гостевого доступа.

Revision ID: 20261001_0005
Revises: 20260917_0004
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261001_0005"
down_revision: str | Sequence[str] | None = "20260917_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Добавляет durable область доступа; старые job остаются без владельца."""
    op.add_column("analysis_jobs", sa.Column("owner_user_id", sa.Uuid(), nullable=True))
    op.add_column(
        "analysis_jobs",
        sa.Column("guest_access_token_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "analysis_jobs",
        sa.Column("guest_access_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_analysis_jobs_guest_access_pair",
        "analysis_jobs",
        "(guest_access_token_hash IS NULL AND guest_access_expires_at IS NULL) "
        "OR (guest_access_token_hash IS NOT NULL "
        "AND guest_access_expires_at IS NOT NULL AND owner_user_id IS NULL)",
    )
    op.create_index(
        "ix_analysis_jobs_owner_user_id", "analysis_jobs", ["owner_user_id"]
    )


def downgrade() -> None:
    """Удаляет данные доступа при откате миграции."""
    op.drop_index("ix_analysis_jobs_owner_user_id", table_name="analysis_jobs")
    op.drop_constraint(
        "ck_analysis_jobs_guest_access_pair", "analysis_jobs", type_="check"
    )
    op.drop_column("analysis_jobs", "guest_access_expires_at")
    op.drop_column("analysis_jobs", "guest_access_token_hash")
    op.drop_column("analysis_jobs", "owner_user_id")
