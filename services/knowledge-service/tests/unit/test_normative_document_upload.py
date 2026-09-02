# services/knowledge-service/tests/unit/test_normative_document_upload.py

"""Unit tests managed upload нормативных документов."""

from collections.abc import Callable
from dataclasses import (
    dataclass,
    field,
)
from datetime import (
    UTC,
    datetime,
)
from hashlib import sha256
from types import TracebackType
from uuid import UUID

import pytest
from pdrd_knowledge_service.application.use_cases.normative_documents import (
    GetNormativeDocumentContent,
    NormativeDocumentCategoryError,
    NormativeDocumentUploadError,
    UploadNormativeDocument,
)
from pdrd_knowledge_service.domain.normative_catalog import (
    IndexingStatus,
    NormativeCategory,
    NormativeDocument,
    NormativeSection,
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

OTHER_SECTION_ID = UUID(
    "22222222-2222-2222-2222-222222222222",
)

CATEGORY_ID = UUID(
    "33333333-3333-3333-3333-333333333333",
)

DOCUMENT_ID = UUID(
    "55555555-5555-5555-5555-555555555555",
)


@dataclass
class FakeCatalogState:
    """In-memory state managed document tests."""

    sections: dict[
        UUID,
        NormativeSection,
    ] = field(
        default_factory=dict,
    )

    categories: dict[
        UUID,
        NormativeCategory,
    ] = field(
        default_factory=dict,
    )

    documents: dict[
        UUID,
        NormativeDocument,
    ] = field(
        default_factory=dict,
    )

    commits: int = 0

    fail_document_add: bool = False


class FakeSectionRepository:
    """Минимальный repository sections."""

    def __init__(
        self,
        state: FakeCatalogState,
    ) -> None:
        """Сохраняет state."""
        self._state = state

    async def get(
        self,
        section_id: UUID,
    ) -> NormativeSection | None:
        """Возвращает section."""
        return self._state.sections.get(
            section_id,
        )


class FakeCategoryRepository:
    """Минимальный repository categories."""

    def __init__(
        self,
        state: FakeCatalogState,
    ) -> None:
        """Сохраняет state."""
        self._state = state

    async def get(
        self,
        category_id: UUID,
    ) -> NormativeCategory | None:
        """Возвращает category."""
        return self._state.categories.get(
            category_id,
        )


class FakeDocumentRepository:
    """In-memory repository documents."""

    def __init__(
        self,
        state: FakeCatalogState,
    ) -> None:
        """Сохраняет state."""
        self._state = state

    async def add(
        self,
        document: NormativeDocument,
    ) -> None:
        """Добавляет document либо эмулирует DB failure."""
        if self._state.fail_document_add:
            raise RuntimeError(
                "Database failure.",
            )

        self._state.documents[document.document_id] = document

    async def get(
        self,
        document_id: UUID,
    ) -> NormativeDocument | None:
        """Возвращает document."""
        return self._state.documents.get(
            document_id,
        )


class FakeUnitOfWork:
    """Fake Unit of Work managed document tests."""

    def __init__(
        self,
        state: FakeCatalogState,
    ) -> None:
        """Создаёт repositories."""
        self._state = state

        self.sections = FakeSectionRepository(
            state,
        )

        self.categories = FakeCategoryRepository(
            state,
        )

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
        """Учитывает commit."""
        self._state.commits += 1

    async def rollback(
        self,
    ) -> None:
        """Fake rollback не требуется."""
        return None


class FakeDocumentStorage:
    """In-memory physical storage."""

    def __init__(
        self,
    ) -> None:
        """Создаёт пустой storage."""
        self.saved: dict[
            str,
            bytes,
        ] = {}

        self.deleted: list[str] = []

    async def save(
        self,
        *,
        storage_key: str,
        content: bytes,
    ) -> None:
        """Сохраняет bytes."""
        self.saved[storage_key] = content

    async def read(
        self,
        *,
        storage_key: str,
    ) -> bytes:
        """Возвращает bytes."""
        return self.saved[storage_key]

    async def delete(
        self,
        *,
        storage_key: str,
    ) -> None:
        """Удаляет bytes."""
        self.saved.pop(
            storage_key,
            None,
        )

        self.deleted.append(
            storage_key,
        )


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


def make_section(
    *,
    section_id: UUID = SECTION_ID,
) -> NormativeSection:
    """Создаёт section."""
    return NormativeSection(
        section_id=section_id,
        name="ЭОМ",
        system_prompt="Test prompt.",
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )


def make_category(
    *,
    section_id: UUID = SECTION_ID,
) -> NormativeCategory:
    """Создаёт category."""
    return NormativeCategory(
        category_id=CATEGORY_ID,
        section_id=section_id,
        parent_id=None,
        name="СП",
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )


def make_document() -> NormativeDocument:
    """Создаёт managed document metadata."""
    return NormativeDocument(
        document_id=DOCUMENT_ID,
        section_id=SECTION_ID,
        category_id=None,
        original_name="СП 256.pdf",
        storage_key=(f"{SECTION_ID}/{DOCUMENT_ID}.pdf"),
        mime_type="application/pdf",
        size_bytes=20,
        sha256="a" * 64,
        index_status=IndexingStatus.UPLOADED,
        index_error=None,
        indexed_at=None,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )


@pytest.mark.asyncio
async def test_upload_saves_pdf_and_metadata() -> None:
    """Upload сохраняет PDF и metadata со статусом uploaded."""
    state = FakeCatalogState()

    state.sections[SECTION_ID] = make_section()

    storage = FakeDocumentStorage()

    content = b"%PDF-1.7\ntest document\n%%EOF"

    use_case = UploadNormativeDocument(
        unit_of_work_factory=build_factory(
            state,
        ),
        storage=storage,
        max_upload_bytes=1024,
        clock=lambda: BASE_TIME,
        identifier_factory=lambda: DOCUMENT_ID,
    )

    document = await use_case.execute(
        section_id=SECTION_ID,
        category_id=None,
        original_name="../../СП 256.pdf",
        content=content,
    )

    expected_key = f"{SECTION_ID}/{DOCUMENT_ID}.pdf"

    assert document.document_id == DOCUMENT_ID
    assert document.original_name == "СП 256.pdf"
    assert document.storage_key == expected_key
    assert document.index_status is IndexingStatus.UPLOADED
    assert (
        document.sha256
        == sha256(
            content,
        ).hexdigest()
    )

    assert storage.saved[expected_key] == content

    assert state.documents[DOCUMENT_ID] == document

    assert state.commits == 1


@pytest.mark.asyncio
async def test_upload_rejects_non_pdf_content() -> None:
    """Upload отвергает файл без PDF signature."""
    state = FakeCatalogState()

    state.sections[SECTION_ID] = make_section()

    storage = FakeDocumentStorage()

    use_case = UploadNormativeDocument(
        unit_of_work_factory=build_factory(
            state,
        ),
        storage=storage,
        max_upload_bytes=1024,
        clock=lambda: BASE_TIME,
        identifier_factory=lambda: DOCUMENT_ID,
    )

    with pytest.raises(
        NormativeDocumentUploadError,
        match="PDF signature",
    ):
        await use_case.execute(
            section_id=SECTION_ID,
            category_id=None,
            original_name="document.pdf",
            content=b"not a pdf",
        )

    assert storage.saved == {}
    assert state.documents == {}


@pytest.mark.asyncio
async def test_upload_rejects_oversized_pdf() -> None:
    """Upload проверяет application size limit."""
    state = FakeCatalogState()

    state.sections[SECTION_ID] = make_section()

    storage = FakeDocumentStorage()

    use_case = UploadNormativeDocument(
        unit_of_work_factory=build_factory(
            state,
        ),
        storage=storage,
        max_upload_bytes=10,
        clock=lambda: BASE_TIME,
        identifier_factory=lambda: DOCUMENT_ID,
    )

    with pytest.raises(
        NormativeDocumentUploadError,
        match="лимит",
    ):
        await use_case.execute(
            section_id=SECTION_ID,
            category_id=None,
            original_name="document.pdf",
            content=(b"%PDF-1.7\ntoo large"),
        )

    assert storage.saved == {}


@pytest.mark.asyncio
async def test_upload_rejects_category_from_other_section() -> None:
    """Document category обязана принадлежать выбранному section."""
    state = FakeCatalogState()

    state.sections[SECTION_ID] = make_section()

    state.sections[OTHER_SECTION_ID] = make_section(
        section_id=OTHER_SECTION_ID,
    )

    state.categories[CATEGORY_ID] = make_category(
        section_id=OTHER_SECTION_ID,
    )

    storage = FakeDocumentStorage()

    use_case = UploadNormativeDocument(
        unit_of_work_factory=build_factory(
            state,
        ),
        storage=storage,
        max_upload_bytes=1024,
        clock=lambda: BASE_TIME,
        identifier_factory=lambda: DOCUMENT_ID,
    )

    with pytest.raises(
        NormativeDocumentCategoryError,
    ):
        await use_case.execute(
            section_id=SECTION_ID,
            category_id=CATEGORY_ID,
            original_name="document.pdf",
            content=b"%PDF-1.7\n%%EOF",
        )

    assert storage.saved == {}


@pytest.mark.asyncio
async def test_upload_removes_file_when_database_insert_fails() -> None:
    """DB failure компенсируется удалением физического PDF."""
    state = FakeCatalogState(
        fail_document_add=True,
    )

    state.sections[SECTION_ID] = make_section()

    storage = FakeDocumentStorage()

    use_case = UploadNormativeDocument(
        unit_of_work_factory=build_factory(
            state,
        ),
        storage=storage,
        max_upload_bytes=1024,
        clock=lambda: BASE_TIME,
        identifier_factory=lambda: DOCUMENT_ID,
    )

    expected_key = f"{SECTION_ID}/{DOCUMENT_ID}.pdf"

    with pytest.raises(
        RuntimeError,
        match="Database failure",
    ):
        await use_case.execute(
            section_id=SECTION_ID,
            category_id=None,
            original_name="document.pdf",
            content=b"%PDF-1.7\n%%EOF",
        )

    assert expected_key not in storage.saved
    assert storage.deleted == [
        expected_key,
    ]


@pytest.mark.asyncio
async def test_get_content_reads_file_by_database_storage_key() -> None:
    """Content query использует storage_key из PostgreSQL metadata."""
    state = FakeCatalogState()

    document = make_document()

    state.documents[DOCUMENT_ID] = document

    storage = FakeDocumentStorage()

    storage.saved[document.storage_key] = b"%PDF-1.7\ncontent\n%%EOF"

    result = await GetNormativeDocumentContent(
        unit_of_work_factory=build_factory(
            state,
        ),
        storage=storage,
    ).execute(
        document_id=DOCUMENT_ID,
    )

    assert result.document == document
    assert result.content == b"%PDF-1.7\ncontent\n%%EOF"
