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
                == "20261005_0005"
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
