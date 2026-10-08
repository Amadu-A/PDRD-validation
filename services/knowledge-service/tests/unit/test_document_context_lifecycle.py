# services/knowledge-service/tests/unit/test_document_context_lifecycle.py

"""Временный D: изоляция заданий, повтор, ошибка и ограниченный сбор кандидатов."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pdrd_knowledge_service.application.use_cases.document_context import (
    BuildDocumentContext,
    DocumentContextIndex,
    DocumentContextPage,
    SearchDocumentContext,
)
from pdrd_knowledge_service.domain.search import VectorPoint


class MemoryVectors:
    """Хранит физические коллекции и считает удаления."""

    def __init__(self):
        """Создаёт независимое хранилище, включая чужую коллекцию N."""
        self.records = {"normative": []}
        self.fail_upsert = False

    async def list_collections(self):
        """Возвращает имена без чтения содержимого."""
        return tuple(self.records)

    async def create_collection(self, *, collection, vector_size):
        """Создаёт временную коллекцию."""
        self.records[collection] = []

    async def upsert(self, *, collection, records):
        """Сохраняет данные либо имитирует сбой после создания коллекции."""
        if self.fail_upsert:
            raise RuntimeError("Недоступно")
        self.records[collection].extend(records)

    async def delete_collection(self, *, collection):
        """Удаляет идемпотентно."""
        return self.records.pop(collection, None) is not None

    async def search(self, *, collection, vector, limit):
        """Возвращает сохранённые точки в обратном порядке."""
        return [
            VectorPoint(row.point_id, 0.9, row.payload)
            for row in reversed(self.records[collection][:limit])
        ]


class Embeddings:
    """Заменяет GPU при проверке жизненного цикла."""

    async def embed(self, texts, *, instruction):
        """Строит одинаковые векторы; идентичность источника от них не зависит."""
        return [[1.0, 0.0] for _ in texts]


def builder(store):
    """Собирает производственный сценарий через независимые порты."""
    index = DocumentContextIndex(store, "document_test")
    return BuildDocumentContext(index, Embeddings(), 200, 20, 2, 2)


async def test_same_pdf_jobs_are_isolated_retry_recreates_and_cleanup_is_idempotent():
    """SHA не связывает задания; повтор одного задания не накапливает коллекции."""
    store = MemoryVectors()
    use_case = builder(store)
    first, second = uuid4(), uuid4()
    pages = (
        DocumentContextPage(7, "Б-012 -37", ()),
        DocumentContextPage(10, "Б-012 -35", ()),
    )
    built = await use_case.execute(
        document_id=first, source_sha256="a" * 64, pages=pages
    )
    await use_case.execute(document_id=second, source_sha256="a" * 64, pages=pages)
    assert built.context_id == first and not built.cache_hit
    search = SearchDocumentContext(use_case.index, Embeddings(), "test", 5)
    before = await search.execute(context_id=first, enabled=True, query="Б-012")
    assert {source.source_id for source in before.sources} == {
        "D-p0007-c0",
        "D-p0010-c0",
    }
    rebuilt = await use_case.execute(
        document_id=first, source_sha256="a" * 64, pages=pages
    )
    assert rebuilt.collection_name != built.collection_name
    assert len(store.records) == 3
    after = await search.execute(
        context_id=first, enabled=True, query="другая формулировка"
    )
    assert {source.source_id for source in before.sources} == {
        source.source_id for source in after.sources
    }
    await use_case.index.cleanup(context_id=first)
    await use_case.index.cleanup(context_id=first)
    assert not (
        await search.execute(context_id=first, enabled=True, query="Б-012")
    ).sources
    assert (
        await search.execute(context_id=second, enabled=True, query="Б-012")
    ).sources
    assert "normative" in store.records


async def test_failed_build_removes_partial_collection():
    """Сбой upsert не оставляет незавершённый индекс до страховочной очистки."""
    store = MemoryVectors()
    store.fail_upsert = True
    with pytest.raises(RuntimeError):
        await builder(store).execute(
            document_id=uuid4(),
            source_sha256="a" * 64,
            pages=(DocumentContextPage(7, "План", ()),),
        )
    assert tuple(store.records) == ("normative",)


async def test_stale_scan_is_bounded_cursor_based_and_handles_empty_collections():
    """Возраст берётся из имени даже после падения между create и upsert."""
    store = MemoryVectors()
    index = DocumentContextIndex(store, "document_test")
    old, fresh = uuid4(), uuid4()
    now = datetime.now(UTC)
    for document_id, at in ((old, now - timedelta(days=2)), (fresh, now)):
        store.records[
            f"{index.prefix}{document_id.hex}_{int(at.timestamp() * 1_000_000_000)}"
        ] = []
    seen = []
    cursor = ""
    for _ in range(2):
        values, cursor = await index.stale(
            before=now - timedelta(days=1), limit=1, cursor=cursor
        )
        seen.extend(values)
    assert seen == [old] and cursor == ""
    assert len(store.records) == 3
