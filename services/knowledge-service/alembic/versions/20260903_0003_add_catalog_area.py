# services/knowledge-service/alembic/versions/20260903_0003_add_catalog_area.py

"""Добавляет области normative и user_package.

Revision ID: 20260903_0003
Revises: 20260902_0002
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0003"

down_revision: str | Sequence[str] | None = "20260902_0002"

branch_labels: str | Sequence[str] | None = None

depends_on: str | Sequence[str] | None = None

SCHEMA_NAME = "knowledge"


def upgrade() -> None:
    """Добавляет catalog_area существующим категориям и документам."""
    op.add_column(
        "normative_categories",
        sa.Column(
            "catalog_area",
            sa.String(
                length=32,
            ),
            server_default=sa.text(
                "'normative'",
            ),
            nullable=False,
        ),
        schema=SCHEMA_NAME,
    )

    op.create_check_constraint(
        "ck_normative_categories_catalog_area",
        "normative_categories",
        ("catalog_area IN ('normative', 'user_package')"),
        schema=SCHEMA_NAME,
    )

    op.create_index(
        "ix_normative_categories_section_area",
        "normative_categories",
        [
            "section_id",
            "catalog_area",
        ],
        unique=False,
        schema=SCHEMA_NAME,
    )

    op.add_column(
        "normative_documents",
        sa.Column(
            "catalog_area",
            sa.String(
                length=32,
            ),
            server_default=sa.text(
                "'normative'",
            ),
            nullable=False,
        ),
        schema=SCHEMA_NAME,
    )

    op.create_check_constraint(
        "ck_normative_documents_catalog_area",
        "normative_documents",
        ("catalog_area IN ('normative', 'user_package')"),
        schema=SCHEMA_NAME,
    )

    op.create_index(
        "ix_normative_documents_section_area",
        "normative_documents",
        [
            "section_id",
            "catalog_area",
        ],
        unique=False,
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    """Удаляет разделение managed catalog на области."""
    op.drop_index(
        "ix_normative_documents_section_area",
        table_name="normative_documents",
        schema=SCHEMA_NAME,
    )

    op.drop_constraint(
        "ck_normative_documents_catalog_area",
        "normative_documents",
        type_="check",
        schema=SCHEMA_NAME,
    )

    op.drop_column(
        "normative_documents",
        "catalog_area",
        schema=SCHEMA_NAME,
    )

    op.drop_index(
        "ix_normative_categories_section_area",
        table_name="normative_categories",
        schema=SCHEMA_NAME,
    )

    op.drop_constraint(
        "ck_normative_categories_catalog_area",
        "normative_categories",
        type_="check",
        schema=SCHEMA_NAME,
    )

    op.drop_column(
        "normative_categories",
        "catalog_area",
        schema=SCHEMA_NAME,
    )
