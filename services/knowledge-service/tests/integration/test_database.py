# services/knowledge-service/tests/integration/test_database.py

"""Integration tests нормативного каталога с настоящим PostgreSQL."""

import os
from datetime import UTC, datetime
from functools import partial
from uuid import uuid4

import pytest
from pdrd_knowledge_service.core.settings import Settings
from pdrd_knowledge_service.domain.normative_catalog import (
    IndexingStatus,
    NormativeCategory,
    NormativeDocument,
    NormativeSection,
)
from pdrd_knowledge_service.infrastructure.database.engine import (
    build_async_engine,
    build_session_factory,
)
from pdrd_knowledge_service.infrastructure.database.health import (
    DatabaseReadinessProbe,
)
from pdrd_knowledge_service.infrastructure.database.models import (
    NormativeSectionModel,
)
from pdrd_knowledge_service.infrastructure.database.unit_of_work import (
    SqlAlchemyNormativeCatalogUnitOfWork,
)
from sqlalchemy import delete

RUN_DATABASE_TESTS = (
    os.getenv(
        "PDRD_RUN_DATABASE_TESTS",
        "0",
    )
    == "1"
)

pytestmark = [
    pytest.mark.database,
    pytest.mark.skipif(
        not RUN_DATABASE_TESTS,
        reason=("Database integration tests require PDRD_RUN_DATABASE_TESTS=1."),
    ),
]


async def test_normative_catalog_database_round_trip() -> None:
    """Проверяет PostgreSQL repositories и Unit of Work."""
    settings = Settings(
        _env_file=None,
    )

    engine = build_async_engine(
        settings.database,
    )

    session_factory = build_session_factory(
        engine,
    )

    readiness_probe = DatabaseReadinessProbe(
        engine=engine,
        timeout_seconds=(settings.database.health_timeout_seconds),
    )

    unit_of_work_factory = partial(
        SqlAlchemyNormativeCatalogUnitOfWork,
        session_factory,
    )

    section_id = uuid4()
    category_id = uuid4()
    document_id = uuid4()

    now = datetime.now(
        UTC,
    )

    section = NormativeSection(
        section_id=section_id,
        name="Integration Test",
        system_prompt=("Системный prompt integration test."),
        created_at=now,
        updated_at=now,
    )

    category = NormativeCategory(
        category_id=category_id,
        section_id=section_id,
        parent_id=None,
        name="Тестовая категория",
        created_at=now,
        updated_at=now,
    )

    document = NormativeDocument(
        document_id=document_id,
        section_id=section_id,
        category_id=category_id,
        original_name="integration-test.pdf",
        storage_key=(f"integration/{document_id}.pdf"),
        mime_type="application/pdf",
        size_bytes=1024,
        sha256="a" * 64,
        index_status=IndexingStatus.UPLOADED,
        index_error=None,
        indexed_at=None,
        created_at=now,
        updated_at=now,
    )

    try:
        assert await readiness_probe.is_ready() is True

        async with unit_of_work_factory() as unit_of_work:
            await unit_of_work.sections.add(
                section,
            )

            await unit_of_work.categories.add(
                category,
            )

            await unit_of_work.documents.add(
                document,
            )

            await unit_of_work.commit()

        async with unit_of_work_factory() as unit_of_work:
            loaded_section = await unit_of_work.sections.get(
                section_id,
            )

            loaded_category = await unit_of_work.categories.get(
                category_id,
            )

            loaded_document = await unit_of_work.documents.get(
                document_id,
            )

            categories = await unit_of_work.categories.list_by_section(
                section_id,
            )

            documents = await unit_of_work.documents.list_by_section(
                section_id,
            )

        assert loaded_section == section
        assert loaded_category == category
        assert loaded_document == document

        assert categories == [
            category,
        ]

        assert documents == [
            document,
        ]

    finally:
        async with session_factory() as session:
            await session.execute(
                delete(
                    NormativeSectionModel,
                ).where(NormativeSectionModel.id == section_id)
            )

            await session.commit()

        await engine.dispose()
