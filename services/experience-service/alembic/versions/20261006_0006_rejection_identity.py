# services/experience-service/alembic/versions/20261006_0006_rejection_identity.py

"""Согласует технический ключ каталога с объяснением отказа.

Меняет только content_key строк с сохранённой категорией: UUID, JSONB,
редакции, crop, аудит и версии наборов остаются прежними. Старые строки
без причины сохраняют ключ 0005. Исторические миграции не изменяются.
"""

import hashlib
import json

import sqlalchemy as sa
from alembic import op

revision = "20261006_0006"
down_revision = "20260929_0005"
branch_labels = None
depends_on = None


def _key(source: dict, *, include_feedback: bool = True) -> str:
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
    payload = {key: source.get(key, "") for key in keys}
    for key in ("original_text", "text", "original_basis", "normative_basis"):
        payload[key] = " ".join(payload[key].split())

    def coordinates(boxes):
        """Повторяет нормализацию координат исторической миграции 0005."""
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
    original = coordinates(
        [item["bbox"] for item in source.get("proposed_regions", [])]
    )
    display = source.get("display_regions")
    if original and original != payload["issue_regions"]:
        payload["original_regions"] = original
    if display is not None and coordinates(display) != payload["issue_regions"]:
        payload["display_regions"] = coordinates(display)
    if include_feedback and source.get("reason_category") is not None:
        payload["reason_category"] = source["reason_category"]
        payload["comment"] = source.get("comment", "")
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _backfill(*, include_feedback: bool) -> None:
    """Обновляет только индексный ключ, не уничтожая строки старого каталога."""
    if op.get_context().as_sql:
        op.execute(
            "DO $$ BEGIN IF EXISTS (SELECT 1 FROM experience.catalog_examples "
            "WHERE snapshot->'source'->>'reason_category' IS NOT NULL) THEN "
            "RAISE EXCEPTION 'Для пересчёта ключей каталога запустите Alembic с подключением к БД'; END IF; END $$"
        )
        return
    connection = op.get_bind()
    for row in connection.execute(
        sa.text(
            "SELECT c.id, c.snapshot, r.snapshot AS review FROM experience.catalog_examples c "
            "LEFT JOIN experience.review_sessions r ON r.job_id=c.job_id "
            "WHERE c.snapshot->'source'->>'reason_category' IS NOT NULL"
        )
    ).all():
        source = dict(row.snapshot["source"])
        if (
            source.get("decision") == "rejected"
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
            {"id": row.id, "key": _key(source, include_feedback=include_feedback)},
        )


def upgrade() -> None:
    """Разные объяснения отказа различают примеры без изменения сохранённых снимков."""
    _backfill(include_feedback=True)


def downgrade() -> None:
    """Возвращает ключ 0005, сохраняя категорию и комментарий в исходном JSONB."""
    _backfill(include_feedback=False)
