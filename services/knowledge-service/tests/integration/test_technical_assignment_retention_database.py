# services/knowledge-service/tests/integration/test_technical_assignment_retention_database.py

"""Изолированные PostgreSQL/Qdrant: очистка гостевого ТЗ и сохранность требований владельца."""

import os
from datetime import UTC, datetime
from functools import partial
from uuid import uuid4

import pytest
from pdrd_knowledge_service.application.use_cases.technical_assignment_retention import (
    CleanupTechnicalAssignment,
)
from pdrd_knowledge_service.application.use_cases.technical_assignments import (
    build_technical_assignment_storage_key,
)
from pdrd_knowledge_service.domain.normative_catalog import NormativeSection
from pdrd_knowledge_service.domain.project_context import VectorRecord
from pdrd_knowledge_service.domain.technical_assignment import (
    TechnicalAssignment,
    TechnicalAssignmentIndexStatus,
)
from pdrd_knowledge_service.infrastructure.database.models import NormativeSectionModel
from pdrd_knowledge_service.infrastructure.database.technical_assignment_models import (
    TechnicalAssignmentModel,
)
from pdrd_knowledge_service.infrastructure.database.technical_assignment_persistence import (
    SqlAlchemyTechnicalAssignmentUnitOfWork,
)
from pdrd_knowledge_service.infrastructure.database.unit_of_work import (
    SqlAlchemyNormativeCatalogUnitOfWork,
)
from pdrd_knowledge_service.infrastructure.storage.filesystem import (
    LocalFilesystemNormativeDocumentStorage,
)
from pdrd_knowledge_service.infrastructure.vector_store.qdrant import QdrantVectorStore
from sqlalchemy import delete, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [
    pytest.mark.database,
    pytest.mark.skipif(
        os.getenv("PDRD_RUN_KNOWLEDGE_OWNER_DATABASE_TESTS") != "1"
        or os.getenv("PDRD_RUN_KNOWLEDGE_VECTOR_TESTS") != "1",
        reason="Требуются изолированные PostgreSQL и Qdrant Knowledge Service",
    ),
]


@pytest.mark.asyncio
async def test_retention_source_marker_and_guest_vectors(tmp_path):
    """После 30 дней требования доступны без файла; гостевое ТЗ удаляется без чужих точек."""
    url = make_url(os.environ["KNOWLEDGE_OWNER_TEST_DATABASE_URL"])
    assert (url.host, url.database) == (
        "knowledge-test-postgres",
        "pdrd_knowledge_test",
    )
    vector_url = os.environ["KNOWLEDGE_TEST_QDRANT_URL"]
    assert vector_url == "http://knowledge-test-qdrant:6333"
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    vectors = QdrantVectorStore(
        base_url=vector_url, request_timeout_seconds=5, health_timeout_seconds=1
    )
    storage = LocalFilesystemNormativeDocumentStorage(root_path=tmp_path)
    section = uuid4()
    now = datetime.now(UTC)
    owned, guest = [
        TechnicalAssignment(
            uuid4(),
            uuid4(),
            section,
            "tz.pdf",
            "application/pdf",
            100,
            "a" * 64,
            TechnicalAssignmentIndexStatus.READY,
            None,
            now,
            now,
            now,
        )
        for _ in range(2)
    ]
    collection = "test_retention_" + uuid4().hex
    try:
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    text("SELECT version_num FROM alembic_version_knowledge")
                )
                == "20261006_0007"
            )
        async with SqlAlchemyNormativeCatalogUnitOfWork(sessions) as work:
            await work.sections.add(
                NormativeSection(section, "Раздел", "Промпт", now, now)
            )
            await work.commit()
        async with SqlAlchemyTechnicalAssignmentUnitOfWork(sessions) as work:
            for value in (owned, guest):
                await work.assignments.add(value)
                key = build_technical_assignment_storage_key(
                    analysis_document_id=value.analysis_document_id,
                    technical_assignment_id=value.technical_assignment_id,
                    original_name=value.original_name,
                )
                await storage.save(storage_key=key, content=b"%PDF-source")
            await work.commit()
        await vectors.create_collection(collection=collection, vector_size=2)
        await vectors.upsert(
            collection=collection,
            records=tuple(
                VectorRecord(
                    str(uuid4()),
                    [1.0, 0.0],
                    {"technical_assignment_id": str(value.technical_assignment_id)},
                )
                for value in (owned, guest)
            ),
        )
        cleaner = CleanupTechnicalAssignment(
            partial(SqlAlchemyTechnicalAssignmentUnitOfWork, sessions),
            storage,
            vectors,
            collection,
        )
        for value, purge in ((owned, False), (guest, True), (guest, True)):
            await cleaner.execute(
                technical_assignment_id=value.technical_assignment_id,
                analysis_document_id=value.analysis_document_id,
                sha256=value.sha256,
                purge_metadata=purge,
            )
        async with SqlAlchemyTechnicalAssignmentUnitOfWork(sessions) as work:
            assert (
                await work.assignments.get(owned.technical_assignment_id)
            ).source_removed_at is not None
            assert await work.assignments.get(guest.technical_assignment_id) is None
        assert not list(tmp_path.rglob("*.pdf"))
        remaining = await vectors.scroll_payloads(collection=collection)
        assert len(remaining) == 1 and remaining[0].payload[
            "technical_assignment_id"
        ] == str(owned.technical_assignment_id)
        assert await vectors.collection_exists(collection)
    finally:
        await vectors.delete_collection(collection=collection)
        async with engine.begin() as connection:
            await connection.execute(
                delete(TechnicalAssignmentModel).where(
                    TechnicalAssignmentModel.section_id == section
                )
            )
            await connection.execute(
                delete(NormativeSectionModel).where(NormativeSectionModel.id == section)
            )
        await engine.dispose()
