# services/knowledge-service/tests/integration/test_normative_outbox_database.py

"""Integration test normative transactional outbox PostgreSQL."""

import os
from datetime import (
    UTC,
    datetime,
    timedelta,
)
from functools import partial
from uuid import uuid4

import pytest
from pdrd_knowledge_service.core.settings import Settings
from pdrd_knowledge_service.domain.normative_catalog import (
    IndexingStatus,
    NormativeDocument,
    NormativeSection,
)
from pdrd_knowledge_service.domain.normative_outbox import (
    NormativeOutboxMessage,
)
from pdrd_knowledge_service.infrastructure.database.engine import (
    build_async_engine,
    build_session_factory,
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


async def test_normative_outbox_database_round_trip() -> None:
    """Проверяет durable outbox через настоящий PostgreSQL."""
    settings = Settings(
        _env_file=None,
    )

    engine = build_async_engine(
        settings.database,
    )

    session_factory = build_session_factory(
        engine,
    )

    unit_of_work_factory = partial(
        SqlAlchemyNormativeCatalogUnitOfWork,
        session_factory,
    )

    section_id = uuid4()
    document_id = uuid4()
    message_id = uuid4()

    created_at = datetime.now(
        UTC,
    )

    published_at = created_at + timedelta(
        seconds=1,
    )

    section = NormativeSection(
        section_id=section_id,
        name="Outbox Integration Test",
        system_prompt="Integration test prompt.",
        created_at=created_at,
        updated_at=created_at,
    )

    document = NormativeDocument(
        document_id=document_id,
        section_id=section_id,
        category_id=None,
        original_name="outbox-test.pdf",
        storage_key=(f"integration/{document_id}.pdf"),
        mime_type="application/pdf",
        size_bytes=1024,
        sha256="a" * 64,
        index_status=IndexingStatus.QUEUED,
        index_error=None,
        indexed_at=None,
        created_at=created_at,
        updated_at=created_at,
    )

    message = NormativeOutboxMessage.index_requested(
        message_id=message_id,
        document_id=document_id,
        created_at=created_at,
    )

    try:
        async with unit_of_work_factory() as unit_of_work:
            await unit_of_work.sections.add(
                section,
            )

            await unit_of_work.documents.add(
                document,
            )

            await unit_of_work.outbox.add(
                message,
            )

            await unit_of_work.commit()

        async with unit_of_work_factory() as unit_of_work:
            pending = await unit_of_work.outbox.get_pending(
                limit=100,
            )

            loaded = next(
                candidate for candidate in pending if candidate.message_id == message_id
            )

            loaded.mark_published(
                published_at=published_at,
            )

            await unit_of_work.outbox.update(
                loaded,
            )

            await unit_of_work.commit()

        async with unit_of_work_factory() as unit_of_work:
            pending_after_publish = await unit_of_work.outbox.get_pending(
                limit=100,
            )

        assert all(
            candidate.message_id != message_id for candidate in pending_after_publish
        )

    finally:
        async with session_factory() as session:
            await session.execute(
                delete(
                    NormativeSectionModel,
                ).where(NormativeSectionModel.id == section_id)
            )

            await session.commit()

        await engine.dispose()
