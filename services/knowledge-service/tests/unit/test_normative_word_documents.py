# services/knowledge-service/tests/unit/test_normative_word_documents.py

"""Unit tests DOC/DOCX managed normative support."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import (
    UTC,
    datetime,
    timedelta,
)
from io import BytesIO
from types import TracebackType
from uuid import UUID
from zipfile import (
    ZIP_DEFLATED,
    ZipFile,
)

import pytest
from pdrd_knowledge_service.application.normative_document_formats import (
    DOC_MIME_TYPE,
    DOCX_MIME_TYPE,
    PDF_MIME_TYPE,
    preview_storage_key,
)
from pdrd_knowledge_service.application.use_cases.index_normative_document import (
    IndexNormativeDocument,
)
from pdrd_knowledge_service.application.use_cases.normative_documents import (
    GetNormativeDocumentContent,
    NormativeDocumentContentUnavailableError,
    NormativeDocumentUploadError,
    UploadNormativeDocument,
)
from pdrd_knowledge_service.domain.normative_catalog import (
    IndexingStatus,
    NormativeDocument,
    NormativeSection,
)
from pdrd_knowledge_service.domain.normative_indexing import (
    NormativeTextPage,
)
from pdrd_knowledge_service.domain.project_context import (
    VectorRecord,
)

BASE_TIME = datetime(
    2026,
    9,
    3,
    8,
    0,
    tzinfo=UTC,
)

INDEXING_TIME = BASE_TIME + timedelta(
    seconds=1,
)

READY_TIME = BASE_TIME + timedelta(
    seconds=2,
)

SECTION_ID = UUID(
    "11111111-1111-1111-1111-111111111111",
)

DOCUMENT_ID = UUID(
    "22222222-2222-2222-2222-222222222222",
)

_DOC_SIGNATURE = bytes.fromhex(
    "D0CF11E0A1B11AE1",
)


def make_docx_bytes() -> bytes:
    """Создаёт минимальный OOXML container для upload validation."""
    stream = BytesIO()

    with ZipFile(
        stream,
        mode="w",
        compression=ZIP_DEFLATED,
    ) as archive:
        archive.writestr(
            "[Content_Types].xml",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/'
                'package/2006/content-types">'
                '<Default Extension="xml" '
                'ContentType="application/xml"/>'
                '<Override PartName="/word/document.xml" '
                'ContentType="application/'
                "vnd.openxmlformats-officedocument."
                'wordprocessingml.document.main+xml"/>'
                "</Types>"
            ),
        )

        archive.writestr(
            "word/document.xml",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<w:document xmlns:w="http://schemas.'
                'openxmlformats.org/wordprocessingml/2006/main">'
                "<w:body>"
                "<w:p><w:r><w:t>Normative text</w:t></w:r></w:p>"
                "</w:body>"
                "</w:document>"
            ),
        )

    return stream.getvalue()


@dataclass
class FakeState:
    """In-memory catalog state."""

    section: NormativeSection

    document: NormativeDocument | None = None

    commits: int = 0


class FakeSectionRepository:
    """Fake section repository."""

    def __init__(
        self,
        state: FakeState,
    ) -> None:
        """Сохраняет state."""
        self._state = state

    async def get(
        self,
        section_id: UUID,
    ) -> NormativeSection | None:
        """Возвращает section."""
        if section_id == self._state.section.section_id:
            return self._state.section

        return None


class FakeCategoryRepository:
    """Fake categories."""

    async def get(
        self,
        category_id: UUID,
    ) -> None:
        """Категории в тестах не используются."""
        del category_id

        return None


class FakeDocumentRepository:
    """Fake documents."""

    def __init__(
        self,
        state: FakeState,
    ) -> None:
        """Сохраняет state."""
        self._state = state

    async def add(
        self,
        document: NormativeDocument,
    ) -> None:
        """Сохраняет document."""
        self._state.document = document

    async def get(
        self,
        document_id: UUID,
    ) -> NormativeDocument | None:
        """Возвращает document."""
        if (
            self._state.document is not None
            and self._state.document.document_id == document_id
        ):
            return self._state.document

        return None

    async def update(
        self,
        document: NormativeDocument,
    ) -> None:
        """Обновляет document."""
        self._state.document = document


class PlaceholderRepository:
    """Неиспользуемый repository."""

    pass


class FakeUnitOfWork:
    """Fake catalog UoW."""

    def __init__(
        self,
        state: FakeState,
    ) -> None:
        """Создаёт repositories."""
        self._state = state

        self.sections = FakeSectionRepository(
            state,
        )

        self.categories = FakeCategoryRepository()

        self.documents = FakeDocumentRepository(
            state,
        )

        self.outbox = PlaceholderRepository()

    async def __aenter__(
        self,
    ) -> "FakeUnitOfWork":
        """Открывает transaction."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Закрывает transaction."""
        del (
            exc_type,
            exc_value,
            traceback,
        )

    async def commit(
        self,
    ) -> None:
        """Учитывает commit."""
        self._state.commits += 1

    async def rollback(
        self,
    ) -> None:
        """Fake rollback."""
        return None


class FakeStorage:
    """In-memory storage."""

    def __init__(
        self,
    ) -> None:
        """Создаёт storage."""
        self.files: dict[
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
        self.files[storage_key] = content

    async def read(
        self,
        *,
        storage_key: str,
    ) -> bytes:
        """Возвращает bytes."""
        return self.files[storage_key]

    async def delete(
        self,
        *,
        storage_key: str,
    ) -> None:
        """Идемпотентно удаляет bytes."""
        self.files.pop(
            storage_key,
            None,
        )

        self.deleted.append(
            storage_key,
        )


class FakeOfficeConverter:
    """Fake Word → PDF converter."""

    def __init__(
        self,
    ) -> None:
        """Создаёт converter."""
        self.calls: list[
            tuple[
                bytes,
                str,
            ]
        ] = []

    async def convert_to_pdf(
        self,
        *,
        content: bytes,
        original_name: str,
    ) -> bytes:
        """Возвращает deterministic PDF."""
        self.calls.append(
            (
                content,
                original_name,
            )
        )

        return b"%PDF-word-preview"


class FakePdfExtractor:
    """Fake PDF text extractor."""

    async def extract_pages(
        self,
        *,
        content: bytes,
    ) -> tuple[
        NormativeTextPage,
        ...,
    ]:
        """Возвращает страницу Word PDF-preview."""
        assert content == b"%PDF-word-preview"

        return (
            NormativeTextPage(
                page_number=1,
                text="Converted Word normative text.",
            ),
        )


class FakeEmbeddingProvider:
    """Fake embeddings."""

    async def embed(
        self,
        texts: tuple[
            str,
            ...,
        ],
        *,
        instruction: str | None,
    ) -> list[list[float]]:
        """Возвращает vectors."""
        assert texts == ("Converted Word normative text.",)

        assert instruction is None

        return [
            [
                1.0,
                0.5,
            ]
        ]


class FakeVectorStore:
    """Fake Qdrant."""

    def __init__(
        self,
    ) -> None:
        """Создаёт storage records."""
        self.records: list[VectorRecord] = []

    async def delete_by_filter(
        self,
        *,
        collection: str,
        key: str,
        value: str,
    ) -> None:
        """Fake filtered delete."""
        assert collection == "normative-test"

        assert key == "document_id"

        assert value == str(
            DOCUMENT_ID,
        )

    async def upsert(
        self,
        *,
        collection: str,
        records: tuple[
            VectorRecord,
            ...,
        ],
    ) -> None:
        """Сохраняет records."""
        assert collection == "normative-test"

        self.records.extend(
            records,
        )


def make_section() -> NormativeSection:
    """Создаёт section."""
    return NormativeSection(
        section_id=SECTION_ID,
        name="КИПиА",
        system_prompt="Test prompt.",
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )


def factory(
    state: FakeState,
) -> Callable[
    [],
    FakeUnitOfWork,
]:
    """Создаёт UoW factory."""
    return lambda: FakeUnitOfWork(
        state,
    )


def make_word_document(
    *,
    status: IndexingStatus,
    mime_type: str = DOCX_MIME_TYPE,
    original_name: str = "Normative.docx",
) -> NormativeDocument:
    """Создаёт managed Word document."""
    extension = ".docx" if mime_type == DOCX_MIME_TYPE else ".doc"

    return NormativeDocument(
        document_id=DOCUMENT_ID,
        section_id=SECTION_ID,
        category_id=None,
        original_name=original_name,
        storage_key=(f"{SECTION_ID}/{DOCUMENT_ID}{extension}"),
        mime_type=mime_type,
        size_bytes=100,
        sha256="a" * 64,
        index_status=status,
        index_error=None,
        indexed_at=(BASE_TIME if status is IndexingStatus.READY else None),
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )


@pytest.mark.asyncio
async def test_upload_accepts_docx() -> None:
    """DOCX сохраняется с исходным MIME и storage extension."""
    state = FakeState(
        section=make_section(),
    )

    storage = FakeStorage()

    content = make_docx_bytes()

    document = await UploadNormativeDocument(
        unit_of_work_factory=factory(
            state,
        ),
        storage=storage,
        max_upload_bytes=1024 * 1024,
        clock=lambda: BASE_TIME,
        identifier_factory=lambda: DOCUMENT_ID,
    ).execute(
        section_id=SECTION_ID,
        category_id=None,
        original_name="../../Норматив.docx",
        content=content,
    )

    assert document.original_name == "Норматив.docx"

    assert document.mime_type == DOCX_MIME_TYPE

    assert document.storage_key.endswith(
        ".docx",
    )

    assert storage.files[document.storage_key] == content


@pytest.mark.asyncio
async def test_upload_accepts_legacy_doc() -> None:
    """Legacy DOC определяется по Compound File signature."""
    state = FakeState(
        section=make_section(),
    )

    storage = FakeStorage()

    content = _DOC_SIGNATURE + b"\x00" * 128

    document = await UploadNormativeDocument(
        unit_of_work_factory=factory(
            state,
        ),
        storage=storage,
        max_upload_bytes=1024 * 1024,
        clock=lambda: BASE_TIME,
        identifier_factory=lambda: DOCUMENT_ID,
    ).execute(
        section_id=SECTION_ID,
        category_id=None,
        original_name="Normative.doc",
        content=content,
    )

    assert document.mime_type == DOC_MIME_TYPE

    assert document.storage_key.endswith(
        ".doc",
    )


@pytest.mark.asyncio
async def test_upload_rejects_invalid_docx() -> None:
    """Произвольный ZIP нельзя выдать за Word document."""
    state = FakeState(
        section=make_section(),
    )

    storage = FakeStorage()

    stream = BytesIO()

    with ZipFile(
        stream,
        "w",
    ) as archive:
        archive.writestr(
            "random.txt",
            "not word",
        )

    with pytest.raises(
        NormativeDocumentUploadError,
        match="структуру Word",
    ):
        await UploadNormativeDocument(
            unit_of_work_factory=factory(
                state,
            ),
            storage=storage,
            max_upload_bytes=1024 * 1024,
            clock=lambda: BASE_TIME,
            identifier_factory=lambda: DOCUMENT_ID,
        ).execute(
            section_id=SECTION_ID,
            category_id=None,
            original_name="fake.docx",
            content=stream.getvalue(),
        )


@pytest.mark.asyncio
async def test_ready_word_content_returns_pdf_preview() -> None:
    """READY Word document открывается как browser PDF-preview."""
    document = make_word_document(
        status=IndexingStatus.READY,
    )

    state = FakeState(
        section=make_section(),
        document=document,
    )

    storage = FakeStorage()

    preview_key = preview_storage_key(
        document.storage_key,
    )

    storage.files[preview_key] = b"%PDF-preview"

    result = await GetNormativeDocumentContent(
        unit_of_work_factory=factory(
            state,
        ),
        storage=storage,
    ).execute(
        document_id=DOCUMENT_ID,
    )

    assert result.mime_type == PDF_MIME_TYPE

    assert result.content == b"%PDF-preview"


@pytest.mark.asyncio
async def test_unindexed_word_preview_is_not_exposed() -> None:
    """До READY browser preview Word недоступен."""
    state = FakeState(
        section=make_section(),
        document=make_word_document(
            status=IndexingStatus.UPLOADED,
        ),
    )

    with pytest.raises(
        NormativeDocumentContentUnavailableError,
    ):
        await GetNormativeDocumentContent(
            unit_of_work_factory=factory(
                state,
            ),
            storage=FakeStorage(),
        ).execute(
            document_id=DOCUMENT_ID,
        )


@pytest.mark.asyncio
async def test_word_indexer_uses_pdf_preview_pipeline() -> None:
    """Word → PDF дальше использует общий page/chunk/Qdrant pipeline."""
    document = make_word_document(
        status=IndexingStatus.QUEUED,
    )

    state = FakeState(
        section=make_section(),
        document=document,
    )

    storage = FakeStorage()

    original_content = make_docx_bytes()

    storage.files[document.storage_key] = original_content

    converter = FakeOfficeConverter()

    vector_store = FakeVectorStore()

    times = iter(
        (
            INDEXING_TIME,
            READY_TIME,
        )
    )

    result = await IndexNormativeDocument(
        unit_of_work_factory=factory(
            state,
        ),
        storage=storage,
        pdf_extractor=FakePdfExtractor(),
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=vector_store,
        collection="normative-test",
        chunk_size=1000,
        chunk_overlap=100,
        embed_batch_size=2,
        upsert_batch_size=2,
        office_converter=converter,
        clock=lambda: next(
            times,
        ),
    ).execute(
        document_id=DOCUMENT_ID,
    )

    assert result.index_status is IndexingStatus.READY

    assert converter.calls == [
        (
            original_content,
            "Normative.docx",
        )
    ]

    preview_key = preview_storage_key(
        document.storage_key,
    )

    assert storage.files[preview_key] == b"%PDF-word-preview"

    assert (
        len(
            vector_store.records,
        )
        == 1
    )

    payload = vector_store.records[0].payload

    assert payload["source_file"] == "Normative.docx"

    assert payload["page"] == 1

    assert payload["text"] == "Converted Word normative text."
