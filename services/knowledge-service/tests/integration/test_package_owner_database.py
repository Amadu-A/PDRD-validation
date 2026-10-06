# services/knowledge-service/tests/integration/test_package_owner_database.py

"""Проверяет UUID владельца после миграции только на отдельной тестовой БД."""

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pdrd_knowledge_service.domain.normative_catalog import (
    CatalogArea,
    IndexingStatus,
    NormativeCategory,
    NormativeDocument,
    NormativeSection,
)
from pdrd_knowledge_service.infrastructure.database.models import NormativeSectionModel
from pdrd_knowledge_service.infrastructure.database.unit_of_work import (
    SqlAlchemyNormativeCatalogUnitOfWork,
)
from sqlalchemy import delete, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [
    pytest.mark.database,
    pytest.mark.skipif(
        os.getenv("PDRD_RUN_KNOWLEDGE_OWNER_DATABASE_TESTS") != "1",
        reason="Требуется отдельный PostgreSQL runner владельцев пакетов",
    ),
]


async def test_package_owner_roundtrip_after_migration() -> None:
    """Владелец папки и документа сохраняется при записи и индексационном обновлении."""
    url = make_url(os.environ["KNOWLEDGE_OWNER_TEST_DATABASE_URL"])
    assert (url.host, url.database, url.username) == (
        "knowledge-test-postgres",
        "pdrd_knowledge_test",
        "knowledge_test",
    )
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    section_id, category_id, document_id, owner = (uuid4() for _ in range(4))
    now = datetime.now(UTC)
    section = NormativeSection(section_id, "Раздел", "Промпт", now, now)
    category = NormativeCategory(
        category_id,
        section_id,
        None,
        "Пакет",
        now,
        now,
        area=CatalogArea.USER_PACKAGE,
        owner_user_id=owner,
    )
    document = NormativeDocument(
        document_id=document_id,
        section_id=section_id,
        category_id=category_id,
        original_name="owner.pdf",
        storage_key="owner.pdf",
        mime_type="application/pdf",
        size_bytes=20,
        sha256="a" * 64,
        index_status=IndexingStatus.UPLOADED,
        index_error=None,
        indexed_at=None,
        created_at=now,
        updated_at=now,
        area=CatalogArea.USER_PACKAGE,
        owner_user_id=owner,
    )
    try:
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    text("SELECT version_num FROM public.alembic_version_knowledge")
                )
                == "20261006_0006"
            )
        async with SqlAlchemyNormativeCatalogUnitOfWork(sessions) as work:
            await work.sections.add(section)
            await work.categories.add(category)
            await work.documents.add(document)
            await work.commit()
        async with SqlAlchemyNormativeCatalogUnitOfWork(sessions) as work:
            assert (await work.categories.get(category_id)).owner_user_id == owner
            stored = await work.documents.get(document_id)
            assert stored.owner_user_id == owner
            await work.documents.update(
                stored.transition_indexing(
                    target_status=IndexingStatus.QUEUED, changed_at=now
                )
            )
            await work.commit()
        async with SqlAlchemyNormativeCatalogUnitOfWork(sessions) as work:
            assert (await work.documents.get(document_id)).owner_user_id == owner
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                delete(NormativeSectionModel).where(
                    NormativeSectionModel.id == section_id
                )
            )
        await engine.dispose()


async def test_postgres_section_lock_waits_for_indexer() -> None:
    """Удаление ждёт shared lock текущего индексатора, другой UUID независим."""
    import asyncio

    from pdrd_knowledge_service.infrastructure.database.section_locks import (
        PostgresCatalogSectionLocks,
    )

    url = make_url(os.environ["KNOWLEDGE_OWNER_TEST_DATABASE_URL"])
    assert (url.host, url.database, url.username) == (
        "knowledge-test-postgres",
        "pdrd_knowledge_test",
        "knowledge_test",
    )
    engine = create_async_engine(url)
    locks = PostgresCatalogSectionLocks(engine)
    section_id = uuid4()
    requested, acquired = asyncio.Event(), asyncio.Event()

    async def delete_scope():
        """Фиксирует фактический вход в эксклюзивную область PostgreSQL."""
        requested.set()
        async with locks.exclusive(section_id):
            acquired.set()

    try:
        async with locks.shared(section_id):
            task = asyncio.create_task(delete_scope())
            await requested.wait()
            # Другой раздел остаётся доступным, даже пока deletion ждёт.
            async with locks.exclusive(uuid4()):
                assert not acquired.is_set()
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(acquired.wait(), timeout=0.1)
        await asyncio.wait_for(task, timeout=2)
        assert acquired.is_set()
        # После exception/cancel session lock не остаётся в пуле.
        with pytest.raises(RuntimeError):
            async with locks.exclusive(section_id):
                raise RuntimeError("test")
        async with locks.exclusive(section_id):
            pass
    finally:
        await engine.dispose()


async def test_cascade_section_delete_postgres_and_filesystem(tmp_path) -> None:
    """Реальные FK CASCADE и файлы очищаются после сбоя, соседний раздел сохранён."""
    from functools import partial

    from pdrd_knowledge_service.application.use_cases.normative_sections import (
        DeleteNormativeSection,
    )
    from pdrd_knowledge_service.infrastructure.database.section_locks import (
        PostgresCatalogSectionLocks,
    )
    from pdrd_knowledge_service.infrastructure.storage.filesystem import (
        LocalFilesystemNormativeDocumentStorage,
    )

    url = make_url(os.environ["KNOWLEDGE_OWNER_TEST_DATABASE_URL"])
    assert (url.host, url.database, url.username) == (
        "knowledge-test-postgres",
        "pdrd_knowledge_test",
        "knowledge_test",
    )
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    factory = partial(SqlAlchemyNormativeCatalogUnitOfWork, sessions)
    target, other, owner = uuid4(), uuid4(), uuid4()
    storage = LocalFilesystemNormativeDocumentStorage(root_path=tmp_path)
    now = datetime.now(UTC)

    class Vectors:
        """Имитирует падение Qdrant до очистки файлов и отслеживает границы удаления."""

        def __init__(self):
            """Готовит сбой первого вызова и журнал фильтров."""
            self.fail = True
            self.filters = []

        async def delete_by_filter(self, **kwargs):
            """Удаляет только выбранный UUID, сохраняя остальные points."""
            self.filters.append(kwargs)
            if self.fail:
                raise RuntimeError("qdrant offline")

    vectors = Vectors()
    try:
        async with factory() as work:
            for section_id in (target, other):
                await work.sections.add(
                    NormativeSection(section_id, str(section_id), "Промпт", now, now)
                )
            for section_id in (target, other):
                for area in CatalogArea:
                    cat, doc = uuid4(), uuid4()
                    await work.categories.add(
                        NormativeCategory(
                            cat,
                            section_id,
                            None,
                            str(cat),
                            now,
                            now,
                            area=area,
                            owner_user_id=owner
                            if area is CatalogArea.USER_PACKAGE
                            else None,
                        )
                    )
                    key = f"{section_id}/{doc}.pdf"
                    await work.documents.add(
                        NormativeDocument(
                            document_id=doc,
                            section_id=section_id,
                            category_id=cat,
                            original_name="test.pdf",
                            storage_key=key,
                            mime_type="application/pdf",
                            size_bytes=10,
                            sha256="a" * 64,
                            index_status=IndexingStatus.QUEUED,
                            index_error=None,
                            indexed_at=None,
                            created_at=now,
                            updated_at=now,
                            area=area,
                            owner_user_id=owner
                            if area is CatalogArea.USER_PACKAGE
                            else None,
                        )
                    )
                    await storage.save(storage_key=key, content=b"%PDF-test")
                    await storage.save(
                        storage_key=key + ".preview.pdf", content=b"%PDF-test"
                    )
            await work.commit()
        # Осиротевший файл не должен пережить каскадную очистку UUID-папки.
        await storage.save(
            storage_key=f"{target}/orphan.preview.pdf", content=b"%PDF-test"
        )
        use_case = DeleteNormativeSection(
            factory, storage, vectors, "shared", PostgresCatalogSectionLocks(engine)
        )
        with pytest.raises(RuntimeError, match="qdrant offline"):
            await use_case.execute(section_id=target)
        async with factory() as work:
            assert (await work.sections.get(target)).deleting
            assert len(await work.documents.list_by_section(target)) == 2
        assert (tmp_path / str(target)).exists()
        vectors.fail = False
        await use_case.execute(section_id=target)
        await use_case.execute(section_id=target)
        async with factory() as work:
            assert await work.sections.get(target) is None
            assert await work.categories.list_by_section(target) == []
            assert await work.documents.list_by_section(target) == []
            assert not (await work.sections.get(other)).deleting
            assert len(await work.documents.list_by_section(other)) == 2
        assert not (tmp_path / str(target)).exists()
        assert len(list((tmp_path / str(other)).iterdir())) == 4
        assert {call["collection"] for call in vectors.filters} == {"shared"}
        assert vectors.filters[-1] == {
            "collection": "shared",
            "key": "section_id",
            "value": str(target),
        }
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                delete(NormativeSectionModel).where(
                    NormativeSectionModel.id.in_((target, other))
                )
            )
        await engine.dispose()
