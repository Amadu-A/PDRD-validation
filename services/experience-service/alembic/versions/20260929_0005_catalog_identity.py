# services/experience-service/alembic/versions/20260929_0005_catalog_identity.py

"""Технический ключ дедупликации без зависимости от заполненности раздела.

Исходные снимки, UUID, редакции, история и версии наборов не изменяются.
Старые дубли сохраняются; дальнейшие прогоны используют самую раннюю запись.
"""

import hashlib
import json

import sqlalchemy as sa
from alembic import op

revision = "20260929_0005"
down_revision = "20260929_0004"
branch_labels = None
depends_on = None


def _key(source: dict, *, legacy: bool = False) -> str:
    """Замороженный алгоритм; миграция не импортирует текущую доменную модель."""
    keys = [
        "source_sha256",
        "page_number",
        "origin",
        "tag",
        "decision",
        "original_text",
        "text",
        "original_basis",
        "normative_basis",
    ]
    if legacy:
        keys.append("section_id")
    payload = {key: source.get(key, "") for key in keys}
    for key in ("original_text", "text", "original_basis", "normative_basis"):
        payload[key] = " ".join(payload[key].split())

    def coordinates(boxes):
        return sorted(
            [
                [
                    round(float(box[key]), 6)
                    for key in ("x_min", "y_min", "x_max", "y_max")
                ]
                for box in boxes
            ]
        )

    payload["issue_regions"] = coordinates(source.get("issue_regions", []))
    if not legacy:
        original = coordinates(
            [item["bbox"] for item in source.get("proposed_regions", [])]
        )
        display = source.get("display_regions")
        if original and original != payload["issue_regions"]:
            payload["original_regions"] = original
        if display is not None and coordinates(display) != payload["issue_regions"]:
            payload["display_regions"] = coordinates(display)
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _backfill(*, legacy: bool) -> None:
    """Обновляет только индексный ключ, не уничтожая строки старого каталога."""
    if op.get_context().as_sql:
        op.execute(
            "DO $$ BEGIN IF EXISTS (SELECT 1 FROM experience.catalog_examples) THEN "
            "RAISE EXCEPTION 'Use online Alembic migration for catalog backfill'; END IF; END $$"
        )
        return
    connection = op.get_bind()
    for row in connection.execute(
        sa.text(
            "SELECT c.id, c.snapshot, r.snapshot AS review FROM experience.catalog_examples c "
            "LEFT JOIN experience.review_sessions r ON r.job_id=c.job_id"
        )
    ).all():
        source = dict(row.snapshot["source"])
        if (
            not legacy
            and source.get("decision") == "rejected"
            and not source.get("proposed_regions")
            and row.review
        ):
            finding = next(
                (
                    item
                    for item in row.review["findings"]
                    if item["finding_id"] == source["finding_id"]
                ),
                None,
            )
            if finding and finding.get("proposed_regions"):
                # Исходная геометрия Review неизменна. Дополняется только ключ;
                # crop и JSON затем восстанавливаются с отдельной редакцией/аудитом.
                source["proposed_regions"] = finding["proposed_regions"]
                source["issue_regions"] = [
                    item["bbox"] for item in finding["proposed_regions"]
                ]
                if row.review["revision"] == source["approved_revision"]:
                    source["display_regions"] = finding.get("display_regions")
        connection.execute(
            sa.text(
                "UPDATE experience.catalog_examples SET content_key=:key WHERE id=:id"
            ),
            {"id": row.id, "key": _key(source, legacy=legacy)},
        )


def upgrade() -> None:
    """Заполнение раздела больше не создаёт дубль одинакового замечания."""
    _backfill(legacy=False)


def downgrade() -> None:
    """Возвращает прежний технический алгоритм, сохраняя все данные."""
    _backfill(legacy=True)
