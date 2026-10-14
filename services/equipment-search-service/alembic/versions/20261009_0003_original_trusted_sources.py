# services/equipment-search-service/alembic/versions/20261009_0003_original_trusted_sources.py

"""Дополняет реестр исходными доверенными сайтами без перезаписи решений пользователей."""

import sqlalchemy as sa
from alembic import op

revision = "20261009_0003"
down_revision = "20261008_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Добавляет точные hostname из исходного списка и аудит только новых записей.

    HTTPS используется и для TDM; разрешение HTTP автоматически не выдаётся.
    Существующие blocked, pending, enabled и allow_http сохраняются как есть.
    WWW указан отдельно для адресов, явно присутствующих в исходных URL.
    """
    sources = (
        ("segnetics", "segnetics.com", "https://segnetics.com/ru/"),
        ("segnetics", "www.segnetics.com", "https://www.segnetics.com/ru/"),
        ("chint", "chint.ru", "https://chint.ru/"),
        ("shenler", "shenler.com", "https://shenler.com/ru/"),
        ("shenler", "www.shenler.com", "https://www.shenler.com/ru/"),
        ("iek", "iek.ru", "https://iek.ru/"),
        ("iek", "www.iek.ru", "https://www.iek.ru/"),
        ("tdm electric", "tdme.ru", "https://tdme.ru/"),
        ("tdm electric", "www.tdme.ru", "https://www.tdme.ru/"),
        ("cnc electric", "cncele.com", "https://cncele.com/"),
        ("cnc electric", "www.cncele.com", "https://www.cncele.com/"),
        ("mean well", "meanwell.com", "https://meanwell.com/"),
        ("mean well", "www.meanwell.com", "https://www.meanwell.com/"),
    )
    bind = op.get_bind()
    statement = sa.text("""
        WITH added AS (
            INSERT INTO equipment_search.source_domains
                (id, manufacturer_key, hostname, status, enabled, allow_http,
                 registration_source, example_url, example_model, updated_at, updated_by)
            VALUES (gen_random_uuid(), :key, :hostname, 'trusted', true, false,
                    :url, :url, '', now(), 'seed-original-registry')
            ON CONFLICT (manufacturer_key, hostname) DO NOTHING
            RETURNING id
        )
        INSERT INTO equipment_search.source_audit
            (id, source_domain_id, previous_status, new_status,
             previous_enabled, new_enabled, previous_allow_http, new_allow_http,
             reason, actor, occurred_at)
        SELECT gen_random_uuid(), id, NULL, 'trusted', NULL, true, NULL, false,
               'Источник из первоначального подтверждённого реестра',
               'seed-original-registry', now()
        FROM added
    """)
    for key, hostname, url in sources:
        bind.execute(statement, {"key": key, "hostname": hostname, "url": url})


def downgrade() -> None:
    """Сохраняет источники и аудит: они могут использоваться сохранёнными документами."""
