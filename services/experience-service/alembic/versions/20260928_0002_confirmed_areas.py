# services/experience-service/alembic/versions/20260928_0002_confirmed_areas.py

"""Хранение областей, подтверждённых инженером, и истории их исправлений.

Revision ID: 20260928_0002
Revises: 20260925_0001

Миграция не изменяет существующие данные Review или инфраструктуру других
сервисов. Выполнять сначала только на изолированной тестовой БД.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20260928_0002"
down_revision: str | Sequence[str] | None = "20260925_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Добавляет актуальную геометрию и отдельный append-only аудит."""
    op.create_table(
        "confirmed_areas",
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "finding_id",
            sa.String(256),
            primary_key=True,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("content_signature", sa.String(64), nullable=False),
        sa.Column("review_revision", sa.Integer(), nullable=False),
        sa.Column("regions", JSONB(), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("note", sa.String(1000), nullable=False),
        sa.Column("confirmed_by", sa.String(128), nullable=False),
        sa.Column(
            "confirmed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["experience.review_sessions.job_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name="ck_confirmed_areas_revision",
        ),
        schema="experience",
    )

    op.create_table(
        "area_confirmation_events",
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "finding_id",
            sa.String(256),
            primary_key=True,
        ),
        sa.Column(
            "revision",
            sa.Integer(),
            primary_key=True,
        ),
        sa.Column(
            "review_revision",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("details", JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id", "finding_id"],
            [
                "experience.confirmed_areas.job_id",
                "experience.confirmed_areas.finding_id",
            ],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name="ck_area_events_revision",
        ),
        schema="experience",
    )


def downgrade() -> None:
    """Только для отдельного тестового окружения; на production не запускать."""
    op.drop_table(
        "area_confirmation_events",
        schema="experience",
    )
    op.drop_table(
        "confirmed_areas",
        schema="experience",
    )
