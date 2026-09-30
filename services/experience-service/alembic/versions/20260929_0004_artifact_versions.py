# services/experience-service/alembic/versions/20260929_0004_artifact_versions.py

"""Разделы нормативки, удаление, повторные прогоны и реестр версий.

Revision ID: 20260929_0004
Revises: 20260928_0003

Сохраняет существующие редакции и аудит. Старые дубли не удаляет: новые прогоны
сопоставляются с самой ранней записью. Полный PDF и бинарные веса в SQL не хранятся.
"""

import hashlib
import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20260929_0004"
down_revision = "20260928_0003"
branch_labels = None
depends_on = None
SCHEMA = "experience"


def _key(source: dict) -> str:
    """Замороженный алгоритм v1: изменение доменного кода не меняет старую миграцию."""
    payload = {
        key: source.get(key, "")
        for key in (
            "source_sha256",
            "section_id",
            "page_number",
            "origin",
            "tag",
            "decision",
            "original_text",
            "text",
            "original_basis",
            "normative_basis",
        )
    }
    for key in ("original_text", "text", "original_basis", "normative_basis"):
        payload[key] = " ".join(payload[key].split())
    payload["issue_regions"] = sorted(
        [
            [round(float(box[key]), 6) for key in ("x_min", "y_min", "x_max", "y_max")]
            for box in source.get("issue_regions", [])
        ]
    )
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def upgrade() -> None:
    """Добавляет только собственные таблицы и обратную совместимость старых снимков."""
    for column in (
        sa.Column("content_key", sa.String(64), nullable=True),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("section_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("section_title", sa.String(300), nullable=False, server_default=""),
    ):
        op.add_column("catalog_examples", column, schema=SCHEMA)
    connection = op.get_bind()
    if op.get_context().as_sql:
        # Бэкфилл JSON требует Python. Offline SQL годится для пустой схемы;
        # непустой каталог должен мигрировать обычной online командой Alembic.
        op.execute(
            "DO $$ BEGIN IF EXISTS (SELECT 1 FROM experience.catalog_examples) THEN "
            "RAISE EXCEPTION 'Use online Alembic migration for catalog backfill'; END IF; END $$"
        )
    else:
        records = connection.execute(
            sa.text("SELECT id, snapshot FROM experience.catalog_examples")
        ).all()
        for record in records:
            connection.execute(
                sa.text(
                    "UPDATE experience.catalog_examples SET content_key=:key WHERE id=:id"
                ),
                {"id": record.id, "key": _key(record.snapshot["source"])},
            )
    op.alter_column("catalog_examples", "content_key", nullable=False, schema=SCHEMA)
    for name in ("content_key", "section_id"):
        op.create_index(
            f"ix_experience_catalog_examples_{name}",
            "catalog_examples",
            [name],
            schema=SCHEMA,
        )
    op.create_table(
        "catalog_occurrences",
        sa.Column(
            "example_id",
            UUID(as_uuid=True),
            sa.ForeignKey("experience.catalog_examples.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("job_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("finding_id", sa.String(256), primary_key=True),
        sa.Column("approved_revision", sa.Integer(), primary_key=True),
        sa.Column("area_revision", sa.Integer(), nullable=False),
        sa.Column("area_signature", sa.String(64), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("snapshot", JSONB(), nullable=False),
        schema=SCHEMA,
    )
    connection.execute(
        sa.text("""
        INSERT INTO experience.catalog_occurrences
        SELECT id, job_id, finding_id, approved_revision, area_revision, area_signature,
               snapshot->'source'->>'source_sha256', snapshot->>'curated_by', snapshot->'source'
        FROM experience.catalog_examples
    """)
    )
    op.create_table(
        "artifact_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("section_id", sa.String(128), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.Column("snapshot", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=False, server_default=""),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('vector', 'fine_tune')", name="ck_artifact_kind"),
        sa.CheckConstraint("revision >= 0", name="ck_artifact_revision"),
        schema=SCHEMA,
    )
    for column in ("section_id", "status"):
        op.create_index(
            f"ix_experience_artifact_versions_{column}",
            "artifact_versions",
            [column],
            schema=SCHEMA,
        )
    op.create_table(
        "artifact_members",
        sa.Column(
            "version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("experience.artifact_versions.id"),
            primary_key=True,
        ),
        sa.Column(
            "example_id",
            UUID(as_uuid=True),
            sa.ForeignKey("experience.catalog_examples.id"),
            primary_key=True,
        ),
        sa.Column("example_revision", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("snapshot", JSONB(), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "applied_artifacts",
        sa.Column("kind", sa.String(16), primary_key=True),
        sa.Column("section_id", sa.String(128), primary_key=True),
        sa.Column(
            "version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("experience.artifact_versions.id"),
            nullable=False,
        ),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "artifact_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("experience.artifact_versions.id"),
            nullable=False,
        ),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot", JSONB(), nullable=False),
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Откат удаляет реестр и происхождение повторных прогонов; применять только осознанно."""
    for name in (
        "artifact_events",
        "applied_artifacts",
        "artifact_members",
        "artifact_versions",
        "catalog_occurrences",
    ):
        op.drop_table(name, schema=SCHEMA)
    for name in ("section_title", "section_id", "deleted", "content_key"):
        op.drop_column("catalog_examples", name, schema=SCHEMA)
