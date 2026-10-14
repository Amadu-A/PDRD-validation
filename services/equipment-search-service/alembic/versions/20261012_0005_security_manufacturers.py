# services/equipment-search-service/alembic/versions/20261012_0005_security_manufacturers.py

"""Дополняет реестр подтверждёнными производителями систем безопасности."""

import json

import sqlalchemy as sa
from alembic import op

revision = "20261012_0005"
down_revision = "20261012_0004"
branch_labels = None
depends_on = None

# Сайты проверены по паспортам и каталогам производителей, не по предположению о модели.
REGISTRY = (
    (
        "Болид",
        ("болид", "bolid"),
        ("bolid.ru", "shop.bolid.ru"),
        "https://bolid.ru/files/373/566/rip_12_51_ret_jun_24.pdf",
    ),
    (
        "РИЭЛТА",
        ("риэлта", "риелта", "rielta"),
        ("rielta.ru",),
        "https://rielta.ru/catalog/izveshchateli/optiko-elektronnye/piron-sh/",
    ),
    (
        "Аргус-Спектр",
        ("аргус-спектр", "argus-spectr"),
        ("catalog.argus-spectr.ru",),
        "https://catalog.argus-spectr.ru/storage/app/uploads/public/641/d62/202/641d622021180068028552.pdf",
    ),
    (
        "Магнито-Контакт",
        ("магнито-контакт", "m-kontakt"),
        ("m-kontakt.ru",),
        "https://m-kontakt.ru/file/detectors_21.pdf",
    ),
    (
        "Электротехника и Автоматика",
        ("электротехника и автоматика", "omelta"),
        ("omelta.com",),
        "https://omelta.com/dokumentatsiya/",
    ),
)


def upgrade() -> None:
    """Добавляет новые записи и аудит, сохраняя ранее принятые решения."""
    bind = op.get_bind()
    statement = sa.text("""
        WITH existing AS (
            SELECT normalized_name FROM equipment_search.manufacturers
            WHERE normalized_name IN (SELECT jsonb_array_elements_text(CAST(:aliases AS JSONB)))
               OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(CAST(:aliases AS JSONB)) AS a(value)
                          WHERE aliases ? a.value)
            ORDER BY (normalized_name = :key) DESC, normalized_name LIMIT 1
        ), updated AS (
            UPDATE equipment_search.manufacturers SET aliases = (
                SELECT jsonb_agg(DISTINCT value) FROM jsonb_array_elements_text(
                    aliases || CAST(:aliases AS JSONB)) AS a(value))
            WHERE normalized_name = (SELECT normalized_name FROM existing)
            RETURNING normalized_name
        ), inserted AS (
            INSERT INTO equipment_search.manufacturers
            (id, name, normalized_name, aliases, enabled, registration_source, created_at)
            SELECT gen_random_uuid(), :name, :key, CAST(:aliases AS JSONB), true, :evidence, now()
            WHERE NOT EXISTS (SELECT 1 FROM existing)
            ON CONFLICT (normalized_name) DO NOTHING
            RETURNING normalized_name
        ), makers AS (
            SELECT normalized_name FROM updated UNION ALL SELECT normalized_name FROM inserted
        ), added AS (
            INSERT INTO equipment_search.source_domains
                    (id, manufacturer_key, hostname, status, enabled, allow_http,
                     registration_source, example_url, example_model, updated_at, updated_by)
            SELECT gen_random_uuid(), makers.normalized_name, h.value, 'trusted', true, false,
                   :evidence, :evidence, '', now(), 'seed-security-registry'
            FROM makers CROSS JOIN jsonb_array_elements_text(CAST(:hosts AS JSONB)) AS h(value)
            ON CONFLICT (manufacturer_key, hostname) DO NOTHING RETURNING id
        )
        INSERT INTO equipment_search.source_audit
                (id, source_domain_id, new_status, new_enabled, new_allow_http, reason, actor, occurred_at)
        SELECT gen_random_uuid(), id, 'trusted', true, false,
               'Официальный документ производителя', 'seed-security-registry', now() FROM added
    """)
    for name, aliases, hosts, evidence in REGISTRY:
        bind.execute(
            statement,
            {
                "key": name.casefold(),
                "name": name,
                "aliases": json.dumps(aliases),
                "hosts": json.dumps(hosts),
                "evidence": evidence,
            },
        )


def downgrade() -> None:
    """Сохраняет используемые snapshot домены и историю доверия при откате."""
