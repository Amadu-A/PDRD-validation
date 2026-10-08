# services/equipment-search-service/alembic/versions/20261008_0001_equipment_catalog.py

"""Каталог производителей, доменов, версий документов и аудит решений.

Revision ID: 20261008_0001
Revises:
"""

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20261008_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Создаёт собственные таблицы без изменения схем других сервисов."""
    op.create_table(
        "manufacturers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False, unique=True),
        sa.Column("aliases", JSONB(), nullable=False, server_default="[]"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("registration_source", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="equipment_search",
    )
    op.create_table(
        "source_domains",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("manufacturer_key", sa.Text(), nullable=False),
        sa.Column("hostname", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "allow_http", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("registration_source", sa.Text(), nullable=False),
        sa.Column("example_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("example_model", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "manufacturer_key", "hostname", name="uq_equipment_source_host"
        ),
        sa.CheckConstraint(
            "status IN ('trusted', 'pending', 'blocked')",
            name="ck_equipment_source_status",
        ),
        schema="equipment_search",
    )
    op.create_table(
        "source_audit",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source_domain_id", UUID(as_uuid=True), nullable=False),
        sa.Column("previous_status", sa.String(16), nullable=True),
        sa.Column("new_status", sa.String(16), nullable=False),
        sa.Column("previous_enabled", sa.Boolean(), nullable=True),
        sa.Column("new_enabled", sa.Boolean(), nullable=False),
        sa.Column("previous_allow_http", sa.Boolean(), nullable=True),
        sa.Column("new_allow_http", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_domain_id"],
            ["equipment_search.source_domains.id"],
        ),
        schema="equipment_search",
    )
    op.create_table(
        "documents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source_id", sa.String(80), nullable=False, unique=True),
        sa.Column("source_domain_id", UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_domain_id"],
            ["equipment_search.source_domains.id"],
        ),
        sa.Column("manufacturer_key", sa.Text(), nullable=False),
        sa.Column("manufacturer", sa.Text(), nullable=False),
        sa.Column("model_key", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("variant_key", sa.Text(), nullable=False),
        sa.Column("variant", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("final_url", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("media_type", sa.String(32), nullable=False),
        sa.Column("storage_reference", sa.Text(), nullable=False),
        sa.Column("revision", sa.Text(), nullable=False),
        sa.Column("publication_date", sa.Text(), nullable=False),
        sa.Column("trust_status", sa.String(32), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("extracted_pages", JSONB(), nullable=False),
        sa.UniqueConstraint(
            "manufacturer_key",
            "model_key",
            "variant_key",
            "sha256",
            name="uq_equipment_document_version",
        ),
        schema="equipment_search",
    )
    op.create_index(
        "ix_equipment_documents_lookup",
        "documents",
        ["manufacturer_key", "model_key", "variant_key", "fetched_at"],
        schema="equipment_search",
    )
    # Только точные hostname, проверенные по официальным сайтам производителей.
    official_sources = (
        (
            "Segnetics",
            ("segnetics",),
            "www.segnetics.com",
            "https://www.segnetics.com/",
        ),
        (
            "Segnetics",
            ("segnetics",),
            "dl.segnetics.com",
            "https://dl.segnetics.com/PRODUCTS/SMH5/manual/",
        ),
        ("CHINT", ("chint",), "www.chintglobal.com", "https://www.chintglobal.com/"),
        ("Shenler", ("shenler",), "www.shenler.com", "https://www.shenler.com/"),
        ("IEK", ("iek",), "iek.global", "https://iek.global/"),
        ("TDM Electric", ("tdm", "tdm electric"), "tdme.ru", "https://tdme.ru/"),
        (
            "CNC Electric",
            ("cnc", "cnc electric"),
            "www.cncele.com",
            "https://www.cncele.com/",
        ),
        (
            "MEAN WELL",
            ("mean well", "meanwell"),
            "www.meanwell.com",
            "https://www.meanwell.com/",
        ),
    )
    bind = op.get_bind()
    for manufacturer, aliases, hostname, official_url in official_sources:
        key = manufacturer.casefold()
        bind.execute(
            sa.text("""
                INSERT INTO equipment_search.manufacturers
                (id, name, normalized_name, aliases, enabled,
                 registration_source, created_at)
                VALUES (gen_random_uuid(), :name, :key, CAST(:aliases AS JSONB),
                        true, :source, now())
                ON CONFLICT (normalized_name) DO NOTHING
            """),
            {
                "name": manufacturer,
                "key": key,
                "aliases": json.dumps(aliases),
                "source": official_url,
            },
        )
        bind.execute(
            sa.text("""
                INSERT INTO equipment_search.source_domains
                (id, manufacturer_key, hostname, status, enabled, allow_http,
                 registration_source, example_url, example_model, updated_at,
                 updated_by)
                VALUES (gen_random_uuid(), :key, :hostname, 'trusted', true,
                        false, :official_url, :official_url, '', now(), 'seed')
                ON CONFLICT (manufacturer_key, hostname) DO NOTHING
            """),
            {
                "key": key,
                "hostname": hostname,
                "official_url": official_url,
            },
        )
        bind.execute(
            sa.text("""
                INSERT INTO equipment_search.source_audit
                (id, source_domain_id, previous_status, new_status,
                 previous_enabled, new_enabled,
                 previous_allow_http, new_allow_http,
                 reason, actor, occurred_at)
                SELECT gen_random_uuid(), id, NULL, 'trusted',
                       NULL, true, NULL, false,
                       'Проверен официальный сайт производителя',
                       'seed', now()
                FROM equipment_search.source_domains
                WHERE manufacturer_key = :key AND hostname = :hostname
            """),
            {"key": key, "hostname": hostname},
        )
    op.create_table(
        "equipment_jobs",
        sa.Column("job_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("identities", JSONB(), nullable=False),
        sa.Column("allow_unverified", sa.Boolean(), nullable=False),
        sa.Column(
            "cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "event_sequence", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("results", JSONB(), nullable=False, server_default="[]"),
        sa.Column("warning", sa.Text(), nullable=False, server_default=""),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'incomplete', 'cancelled')",
            name="ck_equipment_job_status",
        ),
        schema="equipment_search",
    )
    op.create_table(
        "search_events",
        sa.Column("job_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("sequence", sa.BigInteger(), primary_key=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["equipment_search.equipment_jobs.job_id"],
            ondelete="CASCADE",
        ),
        schema="equipment_search",
    )


def downgrade() -> None:
    """Удаляет собственную схему только по явной команде оператора."""
    for table in (
        "search_events",
        "equipment_jobs",
        "documents",
        "source_audit",
        "source_domains",
        "manufacturers",
    ):
        op.drop_table(table, schema="equipment_search")
