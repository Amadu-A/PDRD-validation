# services/knowledge-service/tests/unit/test_normative_document_queries.py

"""Unit tests read use cases нормативных документов."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import (
    UTC,
    datetime,
)
from types import TracebackType
from uuid import (
    UUID,
    uuid4,
)

import pytest
from pdrd_knowledge_service.application.use_cases.normative_documents import (
    GetNormativeDocument,
    ListNormativeDocuments,
    NormativeDocumentNotFoundError,
)
from pdrd_knowledge_service.application.use_cases.normative_sections import (
    NormativeSectionNotFoundError,
)
from pdrd_knowledge_service.domain.normative_catalog import (
    IndexingStatus,
    NormativeDocument,
    NormativeSection,
)
from pdrd_knowledge_service.transport.http.schemas.normative_documents import (
    NormativeDocumentResponse,
)

BASE_TIME = datetime(
    2026,
    9,
    2,
    12,
    0,
    tzinfo=UTC,
)

SECTION_ID = UUID(
    "11111111-1111-1111-1111-111111111111",
)

DOCUMENT_ID = UUID(
    "55555555-5555-5555-5555-555555555555",
)


@dataclass
class FakeCatalogState:
    """In-memory state document query tests."""

    sections: dict[
        UUID,
        NormativeSection,
    ] = field(
        default_factory=dict,
    )

    documents: dict[
        UUID,
        NormativeDocument,
    ] = field(
        default_factory=dict,
    )


class FakeSectionRepository:
    """Минимальный repository sections."""

    def __init__(
        self,
        state: FakeCatalogState,
    ) -> None:
        """Сохраняет test state."""
        self._state = state

    async def get(
        self,
        section_id: UUID,
    ) -> NormativeSection | None:
        """Возвращает section."""
        return self._state.sections.get(
            section_id,
        )


class FakeDocumentRepository:
    """In-memory repository normative documents."""

    def __init__(
        self,
        state: FakeCatalogState,
    ) -> None:
        """Сохраняет test state."""
        self._state = state

    async def get(
        self,
        document_id: UUID,
    ) -> NormativeDocument | None:
        """Возвращает document."""
        return self._state.documents.get(
            document_id,
        )

    async def list_by_section(
        self,
        section_id: UUID,
    ) -> list[NormativeDocument]:
        """Возвращает documents section."""
        return [
            document
            for document in self._state.documents.values()
            if document.section_id == section_id
        ]


class FakeCategoryRepository:
    """Placeholder category repository."""

    pass


class FakeUnitOfWork:
    """Fake Unit of Work document query tests."""

    def __init__(
        self,
        state: FakeCatalogState,
    ) -> None:
        """Создаёт repositories поверх общего state."""
        self.sections = FakeSectionRepository(
            state,
        )

        self.categories = FakeCategoryRepository()

        self.documents = FakeDocumentRepository(
            state,
        )

    async def __aenter__(
        self,
    ) -> "FakeUnitOfWork":
        """Открывает fake transaction."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Закрывает fake transaction."""
        return None

    async def commit(
        self,
    ) -> None:
        """Read use cases не используют commit."""
        return None

    async def rollback(
        self,
    ) -> None:
        """Read use cases не используют rollback."""
        return None


def build_factory(
    state: FakeCatalogState,
) -> Callable[
    [],
    FakeUnitOfWork,
]:
    """Создаёт fake Unit of Work factory."""
    return lambda: FakeUnitOfWork(
        state,
    )


def make_section() -> NormativeSection:
    """Создаёт test section."""
    return NormativeSection(
        section_id=SECTION_ID,
        name="ЭОМ",
        system_prompt="Test prompt.",
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )


def make_document() -> NormativeDocument:
    """Создаёт test normative document."""
    return NormativeDocument(
        document_id=DOCUMENT_ID,
        section_id=SECTION_ID,
        category_id=None,
        original_name="СП 256.pdf",
        storage_key=f"normative/{DOCUMENT_ID}.pdf",
        mime_type="application/pdf",
        size_bytes=4096,
        sha256="a" * 64,
        index_status=IndexingStatus.UPLOADED,
        index_error=None,
        indexed_at=None,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )


@pytest.mark.asyncio
async def test_list_documents_returns_section_documents() -> None:
    """List query возвращает документы указанного section."""
    state = FakeCatalogState()

    state.sections[SECTION_ID] = make_section()

    document = make_document()

    state.documents[DOCUMENT_ID] = document

    result = await ListNormativeDocuments(
        unit_of_work_factory=build_factory(
            state,
        ),
    ).execute(
        section_id=SECTION_ID,
    )

    assert result == (document,)


@pytest.mark.asyncio
async def test_list_documents_requires_existing_section() -> None:
    """List query возвращает not-found для неизвестного section."""
    state = FakeCatalogState()

    with pytest.raises(
        NormativeSectionNotFoundError,
    ):
        await ListNormativeDocuments(
            unit_of_work_factory=build_factory(
                state,
            ),
        ).execute(
            section_id=SECTION_ID,
        )


@pytest.mark.asyncio
async def test_get_document_returns_metadata() -> None:
    """Get query возвращает существующий document."""
    state = FakeCatalogState()

    document = make_document()

    state.documents[DOCUMENT_ID] = document

    loaded = await GetNormativeDocument(
        unit_of_work_factory=build_factory(
            state,
        ),
    ).execute(
        document_id=DOCUMENT_ID,
    )

    assert loaded == document


@pytest.mark.asyncio
async def test_get_missing_document_fails() -> None:
    """Get query возвращает application not-found."""
    state = FakeCatalogState()

    with pytest.raises(
        NormativeDocumentNotFoundError,
    ):
        await GetNormativeDocument(
            unit_of_work_factory=build_factory(
                state,
            ),
        ).execute(
            document_id=uuid4(),
        )


def test_document_response_hides_internal_storage_metadata() -> None:
    """HTTP response не раскрывает storage_key и sha256."""
    response = NormativeDocumentResponse.from_domain(
        make_document(),
    )

    payload = response.model_dump()

    assert payload["ready_for_analysis"] is False
    assert payload["index_status"] is IndexingStatus.UPLOADED
    assert "storage_key" not in payload
    assert "sha256" not in payload
