# services/experience-service/tests/unit/test_index_projection.py

"""Доверенная проекция E: источник, явная отрицательная разметка и актуальная область."""

from dataclasses import replace
from uuid import UUID

import pytest
from pdrd_experience_service.application.use_cases.index_feed import ReadIndexFeed
from pdrd_experience_service.domain.catalog import CatalogEntry
from pdrd_experience_service.domain.index_projection import index_projection
from pdrd_experience_service.domain.review import Decision, Origin, ReviewConflictError

from .test_catalog import AT, example


@pytest.mark.parametrize(
    "tag,origin",
    [("wise", Origin.VLM), ("edited", Origin.VLM), ("gold", Origin.MANUAL)],
)
def test_positive_projection_preserves_origin_and_full_text(tag, origin):
    """Оригинал хранится для аудита; индексируется актуальная принятая формулировка."""
    record = example(tag=tag, origin=origin)
    data = index_projection(CatalogEntry(record, True))
    assert data["tag"] == tag and data["learning_use"] == "positive"
    assert data["texts"] == [{"target": "revised", "text": record.text}]
    assert data["original_text"] == record.source.original_text
    assert "actor" not in data and "issue_regions" not in data


@pytest.mark.parametrize("target", ["original", "revised", "both"])
def test_edited_rejected_needs_adjudication_until_explicit_target(target):
    """Edited + Rejected сохраняется в каталоге и исключён из автоматического E до уточнения."""
    record = example(tag="edited", decision=Decision.REJECTED)
    assert index_projection(CatalogEntry(record, True)) is None
    record = record.curate(
        fields={"negative_target": target, "rejection_reason": "Ошибка интерпретации"},
        actor="engineer:2",
        at=AT,
    )
    data = index_projection(CatalogEntry(record, True))
    assert data["learning_use"] == "negative"
    assert {value["target"] for value in data["texts"]} == (
        {"original", "revised"} if target == "both" else {target}
    )
    assert data["rejection_reason"] == "Ошибка интерпретации"


def test_bad_indexes_only_original_text():
    """Крестик не назначает текущей формулировке новую отрицательную оценку."""
    record = example(tag="bad", decision=Decision.REJECTED)
    data = index_projection(CatalogEntry(record, True))
    assert data["texts"] == [
        {"target": "original", "text": record.source.original_text}
    ]


@pytest.mark.parametrize(
    "excluded",
    ["inactive", "stale", "no_regions", "no_crop", "pending", "gold_rejected"],
)
def test_ineligible_examples_never_have_index_projection(excluded):
    """Принятие текста без проверенной области не даёт разрешения индексировать."""
    record = example()
    current = True
    if excluded == "inactive":
        record = replace(record, active=False)
    elif excluded == "stale":
        current = False
    elif excluded == "no_regions":
        record = replace(record, source=replace(record.source, issue_regions=()))
    elif excluded == "no_crop":
        record = replace(record, crops=())
    elif excluded == "pending":
        record = replace(
            record, source=replace(record.source, decision=Decision.PENDING)
        )
    else:
        record = example(tag="gold", origin=Origin.MANUAL, decision=Decision.REJECTED)
    assert index_projection(CatalogEntry(record, current)) is None


def test_catalog_curation_changes_fingerprint_without_changing_original():
    """Правка основания требует переиндексации и сохраняет первоначальный источник."""
    record = example()
    old = index_projection(CatalogEntry(record, True))
    changed = record.curate(
        fields={"normative_basis": "СП 2"}, actor="engineer:2", at=AT
    )
    new = index_projection(CatalogEntry(changed, True))
    assert new["fingerprint"] != old["fingerprint"] and new["example_revision"] == 1
    assert (
        new["original_text"] == old["original_text"] and changed.source == record.source
    )


async def test_excluded_page_still_advances_cursor_and_verify_rechecks_currentness():
    """Фильтрация не останавливает обход; ранее выданная ссылка проверяется повторно."""
    first = replace(example(), id=UUID(int=1), active=False)
    second = replace(example(), id=UUID(int=2))

    class Catalog:
        """Порт каталога с одной заведомо исключённой первой страницей."""

        current = True

        async def scan(self, *, after, limit):
            """Продолжает keyset после UUID исключённой записи."""
            return (CatalogEntry(first if after is None else second, self.current),)

        async def get_many(self, example_ids):
            """Позволяет отозвать источник между двумя чтениями без N+1."""
            return (CatalogEntry(second, self.current),)

    catalog = Catalog()
    feed = ReadIndexFeed(catalog, None)
    page = await feed.page(limit=1)
    assert page == {"items": [], "next_after": str(first.id)}
    page = await feed.page(after=first.id, limit=1)
    reference = {
        key: page["items"][0][key]
        for key in ("example_id", "example_revision", "fingerprint")
    }
    assert len(await feed.verify((reference,))) == 1
    catalog.current = False
    assert await feed.verify((reference,)) == ()


async def test_crop_read_rechecks_source_after_storage_io():
    """Отзыв во время чтения PNG приводит к конфликту, а не к отдаче устаревшей области."""
    record = example()

    class Catalog:
        """Меняет актуальность по событию чтения хранилища."""

        current = True

        async def get(self, example_id):
            """Возвращает актуальность только из серверного состояния."""
            return CatalogEntry(record, self.current)

    catalog = Catalog()

    class Crops:
        """Отзыв подтверждения возникает одновременно с IO."""

        async def read(self, crop):
            """Воспроизводит изменение источника во время ожидания диска."""
            catalog.current = False
            return b"png"

    feed = ReadIndexFeed(catalog, Crops())
    with pytest.raises(ReviewConflictError, match="во время"):
        await feed.crop(
            example_id=record.id,
            index=0,
            fingerprint=index_projection(CatalogEntry(record, True))["fingerprint"],
        )
