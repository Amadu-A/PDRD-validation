# services/knowledge-service/alembic/versions/20260902_0001_create_normative_catalog.py

"""Создаёт PostgreSQL каталог нормативной базы.

Revision ID: 20260902_0001
Revises:
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260902_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_NAME = "knowledge"


def upgrade() -> None:
    """Создаёт schema и таблицы нормативного каталога."""
    op.execute(sa.text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA_NAME}"))

    op.create_table(
        "normative_sections",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column(
            "name",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "system_prompt",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(name) <> ''",
            name="ck_normative_sections_name_not_blank",
        ),
        sa.PrimaryKeyConstraint(
            "id",
        ),
        schema=SCHEMA_NAME,
    )

    op.create_index(
        "ix_normative_sections_name",
        "normative_sections",
        ["name"],
        unique=False,
        schema=SCHEMA_NAME,
    )

    op.create_table(
        "normative_categories",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column(
            "section_id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column(
            "parent_id",
            sa.Uuid(),
            nullable=True,
        ),
        sa.Column(
            "name",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(name) <> ''",
            name="ck_normative_categories_name_not_blank",
        ),
        sa.CheckConstraint(
            "parent_id IS NULL OR parent_id <> id",
            name="ck_normative_categories_not_self_parent",
        ),
        sa.ForeignKeyConstraint(
            ["section_id"],
            [f"{SCHEMA_NAME}.normative_sections.id"],
            name="fk_normative_categories_section_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            [f"{SCHEMA_NAME}.normative_categories.id"],
            name="fk_normative_categories_parent_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "id",
        ),
        schema=SCHEMA_NAME,
    )

    op.create_index(
        "ix_normative_categories_section_id",
        "normative_categories",
        ["section_id"],
        unique=False,
        schema=SCHEMA_NAME,
    )

    op.create_index(
        "ix_normative_categories_parent_id",
        "normative_categories",
        ["parent_id"],
        unique=False,
        schema=SCHEMA_NAME,
    )

    op.create_table(
        "normative_documents",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column(
            "section_id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column(
            "category_id",
            sa.Uuid(),
            nullable=True,
        ),
        sa.Column(
            "original_name",
            sa.String(length=512),
            nullable=False,
        ),
        sa.Column(
            "storage_key",
            sa.String(length=512),
            nullable=False,
        ),
        sa.Column(
            "mime_type",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "size_bytes",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "index_status",
            sa.String(length=32),
            server_default=sa.text("'uploaded'"),
            nullable=False,
        ),
        sa.Column(
            "index_error",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "indexed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(original_name) <> ''",
            name="ck_normative_documents_name_not_blank",
        ),
        sa.CheckConstraint(
            "btrim(storage_key) <> ''",
            name="ck_normative_documents_storage_key_not_blank",
        ),
        sa.CheckConstraint(
            "btrim(mime_type) <> ''",
            name="ck_normative_documents_mime_not_blank",
        ),
        sa.CheckConstraint(
            "size_bytes > 0",
            name="ck_normative_documents_size_positive",
        ),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-fA-F]{64}$'",
            name="ck_normative_documents_sha256",
        ),
        sa.CheckConstraint(
            "index_status IN ("
            "'uploaded', "
            "'queued', "
            "'indexing', "
            "'ready', "
            "'failed', "
            "'deleting'"
            ")",
            name="ck_normative_documents_index_status",
        ),
        sa.CheckConstraint(
            "("
            "index_status = 'failed' "
            "AND index_error IS NOT NULL "
            "AND btrim(index_error) <> ''"
            ") OR ("
            "index_status <> 'failed' "
            "AND index_error IS NULL"
            ")",
            name="ck_normative_documents_index_error",
        ),
        sa.CheckConstraint(
            "("
            "index_status = 'ready' "
            "AND indexed_at IS NOT NULL"
            ") OR ("
            "index_status <> 'ready' "
            "AND indexed_at IS NULL"
            ")",
            name="ck_normative_documents_indexed_at",
        ),
        sa.ForeignKeyConstraint(
            ["section_id"],
            [f"{SCHEMA_NAME}.normative_sections.id"],
            name="fk_normative_documents_section_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            [f"{SCHEMA_NAME}.normative_categories.id"],
            name="fk_normative_documents_category_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "id",
        ),
        sa.UniqueConstraint(
            "storage_key",
            name="uq_normative_documents_storage_key",
        ),
        schema=SCHEMA_NAME,
    )

    op.create_index(
        "ix_normative_documents_section_id",
        "normative_documents",
        ["section_id"],
        unique=False,
        schema=SCHEMA_NAME,
    )

    op.create_index(
        "ix_normative_documents_category_id",
        "normative_documents",
        ["category_id"],
        unique=False,
        schema=SCHEMA_NAME,
    )

    op.create_index(
        "ix_normative_documents_index_status",
        "normative_documents",
        ["index_status"],
        unique=False,
        schema=SCHEMA_NAME,
    )

    op.create_index(
        "ix_normative_documents_sha256",
        "normative_documents",
        ["sha256"],
        unique=False,
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    """Удаляет нормативный каталог Knowledge Service."""
    op.drop_table(
        "normative_documents",
        schema=SCHEMA_NAME,
    )

    op.drop_table(
        "normative_categories",
        schema=SCHEMA_NAME,
    )

    op.drop_table(
        "normative_sections",
        schema=SCHEMA_NAME,
    )

    op.execute(sa.text(f"DROP SCHEMA IF EXISTS {SCHEMA_NAME}"))
