# services/equipment-search-service/alembic/versions/20261102_0006_document_assessment.py

"""Сохраняет оценку применимости отдельно от исторического доверия источнику."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261102_0006"
down_revision = "20261012_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Добавляет метаданные без изменения готовых документов и их SHA."""
    op.add_column(
        "documents",
        sa.Column("assessment", JSONB(), nullable=False, server_default="{}"),
        schema="equipment_search",
    )
    # Первичные свидетельства проверены: decast.com/about/ и паспорт ВСКМ90.
    # Ранее принятые pending/blocked решения не повышаются до trusted.
    bind = op.get_bind()
    bind.execute(
        sa.text("""
        INSERT INTO equipment_search.manufacturers
        (id, name, normalized_name, aliases, enabled, registration_source, created_at)
        VALUES (gen_random_uuid(), 'Декаст', 'декаст', '["декаст", "decast"]'::jsonb,
                true, 'https://decast.com/about/', now())
        ON CONFLICT (normalized_name) DO NOTHING
    """)
    )
    bind.execute(
        sa.text("""
        WITH added AS (
            INSERT INTO equipment_search.source_domains
            (id, manufacturer_key, hostname, status, enabled, allow_http,
             registration_source, example_url, example_model, updated_at, updated_by)
            VALUES (gen_random_uuid(), 'декаст', 'decast.com', 'trusted', true, false,
                    'https://decast.com/about/', 'https://decast.com/documentation/', '', now(), 'seed-eq-registry')
            ON CONFLICT (manufacturer_key, hostname) DO NOTHING RETURNING id
        )
        INSERT INTO equipment_search.source_audit
        (id, source_domain_id, new_status, new_enabled, new_allow_http, reason, actor, occurred_at)
        SELECT gen_random_uuid(), id, 'trusted', true, false,
               'Официальный сайт и паспорт изготовителя ВСКМ90', 'seed-eq-registry', now() FROM added
    """)
    )


def downgrade() -> None:
    """Удаляет только новые метаданные; исходные байты сохраняются."""
    op.drop_column("documents", "assessment", schema="equipment_search")
