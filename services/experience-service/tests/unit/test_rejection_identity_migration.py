# services/experience-service/tests/unit/test_rejection_identity_migration.py

"""Ключ миграции согласован с доменом, а пересчёт не меняет JSON, историю и crop."""

from copy import deepcopy
from dataclasses import replace
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest
from pdrd_experience_service.application.catalog_snapshot import example_to_json
from pdrd_experience_service.domain.catalog_identity import (
    content_key,
    snapshot_content_key,
)
from pdrd_experience_service.domain.rejection_feedback import REASON_CATEGORIES
from pdrd_experience_service.domain.review import Decision, ProposedRegion, Rectangle

from .test_catalog import example


def migration(filename):
    """Загружает самостоятельную миграцию без выполнения upgrade и подключения к БД."""
    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / filename
    spec = spec_from_file_location(path.stem, path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CURRENT = migration("20261006_0006_rejection_identity.py")
PREVIOUS = migration("20260929_0005_catalog_identity.py")


@pytest.mark.parametrize("reason", [None, *sorted(REASON_CATEGORIES)])
@pytest.mark.parametrize("moved", [False, True])
def test_frozen_migration_key_matches_domain_and_preserves_0005(reason, moved):
    """Все категории, русский комментарий и перемещение области дают тот же ключ."""
    source = example(tag="bad", decision=Decision.REJECTED).source
    source = replace(
        source,
        reason_category=reason,
        comment="Это резервный насос" if reason else "",
        proposed_regions=(ProposedRegion(source.issue_regions[0], "vlm", 0.9, "vlm"),),
        display_regions=(Rectangle(1, 2, 30, 40),) if moved else None,
    )
    snapshot = example_to_json(replace(example(), source=source))["source"]
    assert CURRENT._key(snapshot) == content_key(source)
    assert CURRENT._key(snapshot, include_feedback=False) == PREVIOUS._key(snapshot)
    if reason is None:
        assert CURRENT._key(snapshot) == PREVIOUS._key(snapshot)
    else:
        assert CURRENT._key(snapshot) != PREVIOUS._key(snapshot)


@pytest.mark.parametrize("operation", ["upgrade", "downgrade"])
@pytest.mark.parametrize("matching_revision", [False, True])
def test_legacy_geometry_backfill_updates_only_key(
    monkeypatch, operation, matching_revision
):
    """Сценарий из серверного лога пересчитывает ключ без переписывания источника."""
    record = example(tag="bad", decision=Decision.REJECTED)
    source = replace(
        record.source,
        reason_category="misunderstood_drawing",
        comment="Это резервный насос",
        proposed_regions=(
            ProposedRegion(record.source.issue_regions[0], "vlm", 0.9, "vlm"),
        ),
        display_regions=(Rectangle(1, 2, 30, 40),),
    )
    expected = example_to_json(replace(record, source=source))["source"]
    legacy = deepcopy(expected)
    legacy["issue_regions"] = []
    for field in ("proposed_regions", "display_regions", "area_source"):
        legacy.pop(field)
    review = {
        "revision": source.approved_revision + (not matching_revision),
        "findings": [
            {
                "finding_id": source.finding_id,
                "proposed_regions": expected["proposed_regions"],
                "display_regions": expected["display_regions"],
            }
        ],
    }
    row = SimpleNamespace(id=record.id, snapshot={"source": legacy}, review=review)
    before = deepcopy(row.__dict__)
    calls = []

    class Connection:
        """Подменяет только SQL-ввод/вывод, сохраняя настоящий алгоритм миграции."""

        def execute(self, statement, params=None):
            """Фиксирует UPDATE и возвращает одну выбранную строку."""
            calls.append((str(statement), params))
            return SimpleNamespace(all=lambda: [row])

    monkeypatch.setattr(
        CURRENT.op, "get_context", lambda: SimpleNamespace(as_sql=False)
    )
    monkeypatch.setattr(CURRENT.op, "get_bind", Connection)
    getattr(CURRENT, operation)()
    assert len(calls) == 2
    assert "reason_category' IS NOT NULL" in calls[0][0]
    assert (
        calls[1][0]
        == "UPDATE experience.catalog_examples SET content_key=:key WHERE id=:id"
    )
    if not matching_revision:
        expected["display_regions"] = None
    key = (
        snapshot_content_key(expected)
        if operation == "upgrade"
        else PREVIOUS._key(expected)
    )
    assert calls[1][1] == {"id": record.id, "key": key}
    assert row.__dict__ == before


def test_offline_sql_requires_online_backfill_for_feedback_rows(monkeypatch):
    """SQL-режим не скрывает необходимость прочитать уже сохранённые объяснения."""
    emitted = []
    monkeypatch.setattr(CURRENT.op, "get_context", lambda: SimpleNamespace(as_sql=True))
    monkeypatch.setattr(CURRENT.op, "execute", emitted.append)
    CURRENT.upgrade()
    assert len(emitted) == 1
    assert "reason_category' IS NOT NULL" in emitted[0]
    assert "RAISE EXCEPTION" in emitted[0]


def test_same_pdf_different_feedback_stays_distinct_after_backfill():
    """Пересчёт не сливает разные объяснения одинаковой ошибки."""
    record = example(tag="bad", decision=Decision.REJECTED)
    first = replace(
        record.source, reason_category="false_positive", comment="Резервный насос"
    )
    second = replace(first, reason_category="wrong_location")
    keys = []
    for source in (first, second):
        data = example_to_json(replace(record, source=source))["source"]
        keys.append(CURRENT._key(data))
        assert keys[-1] == content_key(source)
    assert keys[0] != keys[1]
