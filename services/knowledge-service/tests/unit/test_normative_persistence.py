# services/knowledge-service/tests/unit/test_normative_persistence.py

"""Unit tests PostgreSQL persistence нормативного каталога."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pdrd_knowledge_service.core.settings import DatabaseSettings
from pdrd_knowledge_service.domain.normative_catalog import (
    IndexingStatus,
    NormativeCategory,
    NormativeDocument,
    NormativeSection,
)
from pdrd_knowledge_service.infrastructure.database.base import (
    KNOWLEDGE_SCHEMA,
    Base,
)
from pdrd_knowledge_service.infrastructure.database.engine import (
    build_database_url,
)
from pdrd_knowledge_service.infrastructure.database.models import (
    NormativeCategoryModel,
    NormativeDocumentModel,
    NormativeSectionModel,
)
from pdrd_knowledge_service.infrastructure.database.repositories import (
    SqlAlchemyNormativeCategoryRepository,
    SqlAlchemyNormativeDocumentRepository,
    SqlAlchemyNormativeSectionRepository,
)
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

BASE_TIME = datetime(
    2026,
    9,
    2,
    10,
    0,
    tzinfo=UTC,
)


def test_database_url_uses_asyncpg_and_secret_password() -> None:
    """Database URL собирается без ручной конкатенации credentials."""
    settings = DatabaseSettings(
        host="database.internal",
        port=5544,
        name="knowledge_test",
        user="knowledge",
        password=SecretStr(
            "p@ss:word",
        ),
    )

    url = build_database_url(
        settings,
    )

    assert url.drivername == "postgresql+asyncpg"
    assert url.host == "database.internal"
    assert url.port == 5544
    assert url.database == "knowledge_test"
    assert url.username == "knowledge"
    assert url.password == "p@ss:word"


def test_normative_tables_belong_to_knowledge_schema() -> None:
    """Все ORM tables принадлежат bounded context Knowledge Service."""
    expected = {
        f"{KNOWLEDGE_SCHEMA}.normative_sections",
        f"{KNOWLEDGE_SCHEMA}.normative_categories",
        f"{KNOWLEDGE_SCHEMA}.normative_documents",
    }

    assert (
        set(
            Base.metadata.tables,
        )
        == expected
    )


def test_section_model_maps_back_to_domain() -> None:
    """Section repository восстанавливает чистую domain entity."""
    section_id = uuid4()

    model = NormativeSectionModel(
        id=section_id,
        name="ЭОМ",
        system_prompt="Проверяй нормативные требования.",
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )

    entity = SqlAlchemyNormativeSectionRepository._to_domain(
        model,
    )

    assert entity.section_id == section_id
    assert entity.name == "ЭОМ"
    assert entity.system_prompt == "Проверяй нормативные требования."


def test_category_model_maps_back_to_domain() -> None:
    """Category repository сохраняет hierarchy identifiers."""
    category_id = uuid4()
    section_id = uuid4()
    parent_id = uuid4()

    model = NormativeCategoryModel(
        id=category_id,
        section_id=section_id,
        parent_id=parent_id,
        name="СП",
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )

    entity = SqlAlchemyNormativeCategoryRepository._to_domain(
        model,
    )

    assert entity.category_id == category_id
    assert entity.section_id == section_id
    assert entity.parent_id == parent_id
    assert entity.name == "СП"


def test_document_model_maps_indexing_state_back_to_domain() -> None:
    """Document repository восстанавливает IndexingStatus domain enum."""
    document_id = uuid4()
    section_id = uuid4()
    category_id = uuid4()

    model = NormativeDocumentModel(
        id=document_id,
        section_id=section_id,
        category_id=category_id,
        original_name="СП 256.pdf",
        storage_key=f"{document_id}.pdf",
        mime_type="application/pdf",
        size_bytes=4096,
        sha256="a" * 64,
        index_status=IndexingStatus.READY.value,
        index_error=None,
        indexed_at=BASE_TIME,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )

    entity = SqlAlchemyNormativeDocumentRepository._to_domain(
        model,
    )

    assert entity.document_id == document_id
    assert entity.section_id == section_id
    assert entity.category_id == category_id
    assert entity.index_status is IndexingStatus.READY
    assert entity.ready_for_analysis is True


@pytest.mark.asyncio
async def test_repository_adds_flush_entities_inside_transaction() -> None:
    """Repository add делает flush, но не управляет commit transaction."""
    session = AsyncMock(
        spec=AsyncSession,
    )

    section_id = uuid4()
    category_id = uuid4()
    document_id = uuid4()

    section = NormativeSection(
        section_id=section_id,
        name="ЭОМ",
        system_prompt="Проверяй нормативные требования.",
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )

    category = NormativeCategory(
        category_id=category_id,
        section_id=section_id,
        parent_id=None,
        name="СП",
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )

    document = NormativeDocument(
        document_id=document_id,
        section_id=section_id,
        category_id=category_id,
        original_name="СП 256.pdf",
        storage_key=f"{document_id}.pdf",
        mime_type="application/pdf",
        size_bytes=4096,
        sha256="a" * 64,
        index_status=IndexingStatus.UPLOADED,
        index_error=None,
        indexed_at=None,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )

    section_repository = SqlAlchemyNormativeSectionRepository(
        session,
    )

    category_repository = SqlAlchemyNormativeCategoryRepository(
        session,
    )

    document_repository = SqlAlchemyNormativeDocumentRepository(
        session,
    )

    await section_repository.add(
        section,
    )

    await category_repository.add(
        category,
    )

    await document_repository.add(
        document,
    )

    assert session.add.call_count == 3

    assert session.flush.await_count == 3

    session.commit.assert_not_awaited()
