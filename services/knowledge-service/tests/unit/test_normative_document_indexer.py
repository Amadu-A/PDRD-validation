# services/knowledge-service/tests/unit/test_normative_document_indexer.py

"""Unit tests managed normative indexing worker use case."""

from collections.abc import Callable
from dataclasses import (
    dataclass,
)
from datetime import (
    UTC,
    datetime,
    timedelta,
)
from types import TracebackType
from uuid import UUID

import pytest
from pdrd_knowledge_service.application.ports.embedding import (
    EmbeddingProviderError,
)
from pdrd_knowledge_service.application.use_cases.index_normative_document import (
    IndexNormativeDocument,
    NormativeIndexingExecutionError,
)
from pdrd_knowledge_service.domain.normative_catalog import (
    IndexingStatus,
    NormativeDocument,
)
from pdrd_knowledge_service.domain.normative_indexing import (
    NormativeTextPage,
    stable_normative_point_id,
)
from pdrd_knowledge_service.domain.project_context import (
    VectorRecord,
)

BASE_TIME = datetime(
    2026,
    9,
    2,
    12,
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
    "55555555-5555-5555-5555-555555555555",
)


@dataclass
class FakeState:
    """Общее состояние fake Unit of Work."""

    document: NormativeDocument

    commits: int = 0


class FakeDocumentRepository:
    """Fake document repository."""

    def __init__(
        self,
        state: FakeState,
    ) -> None:
        """Сохраняет state."""
        self._state = state

    async def get(
        self,
        document_id: UUID,
    ) -> NormativeDocument | None:
        """Возвращает единственный test document."""
        if document_id != self._state.document.document_id:
            return None

        return self._state.document

    async def update(
        self,
        document: NormativeDocument,
    ) -> None:
        """Обновляет test document."""
        self._state.document = document


class PlaceholderRepository:
    """Неиспользуемый fake repository."""

    pass


class FakeUnitOfWork:
    """Fake Unit of Work indexing tests."""

    def __init__(
        self,
        state: FakeState,
    ) -> None:
        """Создаёт repositories."""
        self._state = state

        self.sections = PlaceholderRepository()
        self.categories = PlaceholderRepository()
        self.outbox = PlaceholderRepository()

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
        """Fake rollback."""
        return None


class FakeStorage:
    """Fake managed filesystem storage."""

    def __init__(
        self,
    ) -> None:
        """Создаёт fake storage."""
        self.read_count = 0

    async def read(
        self,
        *,
        storage_key: str,
    ) -> bytes:
        """Возвращает test PDF bytes."""
        self.read_count += 1

        assert storage_key.endswith(
            ".pdf",
        )

        return b"%PDF-test-content"


class FakePdfExtractor:
    """Fake PDF text extractor."""

    def __init__(
        self,
    ) -> None:
        """Создаёт fake extractor."""
        self.call_count = 0

    async def extract_pages(
        self,
        *,
        content: bytes,
    ) -> tuple[
        NormativeTextPage,
        ...,
    ]:
        """Возвращает две test pages."""
        self.call_count += 1

        assert content == b"%PDF-test-content"

        return (
            NormativeTextPage(
                page_number=1,
                text="First normative page.",
            ),
            NormativeTextPage(
                page_number=2,
                text="Second normative page.",
            ),
        )


class FakeEmbeddingProvider:
    """Fake embedding provider."""

    def __init__(
        self,
        *,
        fail: bool = False,
    ) -> None:
        """Сохраняет режим fake provider."""
        self._fail = fail

        self.calls: list[tuple[str, ...]] = []

    async def embed(
        self,
        texts: tuple[str, ...],
        *,
        instruction: str | None,
    ) -> list[list[float]]:
        """Возвращает deterministic test vectors."""
        if self._fail:
            raise EmbeddingProviderError(
                "Embedding failure.",
            )

        assert instruction is None

        self.calls.append(
            texts,
        )

        return [
            [
                float(
                    index + 1,
                ),
                0.5,
            ]
            for index in range(
                len(
                    texts,
                )
            )
        ]


class FakeVectorStore:
    """Fake Qdrant adapter."""

    def __init__(
        self,
    ) -> None:
        """Создаёт пустой fake vector store."""
        self.delete_calls: list[
            tuple[
                str,
                str,
                str,
            ]
        ] = []

        self.records: list[VectorRecord] = []

    async def delete_by_filter(
        self,
        *,
        collection: str,
        key: str,
        value: str,
    ) -> None:
        """Учитывает filtered delete."""
        self.delete_calls.append(
            (
                collection,
                key,
                value,
            )
        )

        self.records = [
            record
            for record in self.records
            if record.payload.get(
                key,
            )
            != value
        ]

    async def upsert(
        self,
        *,
        collection: str,
        records: tuple[
            VectorRecord,
            ...,
        ],
    ) -> None:
        """Сохраняет test records."""
        assert collection == "normative-test"

        self.records.extend(
            records,
        )


def build_factory(
    state: FakeState,
) -> Callable[
    [],
    FakeUnitOfWork,
]:
    """Создаёт fake Unit of Work factory."""
    return lambda: FakeUnitOfWork(
        state,
    )


def make_document(
    *,
    status: IndexingStatus,
) -> NormativeDocument:
    """Создаёт test document нужного lifecycle status."""
    indexed_at = BASE_TIME if status is IndexingStatus.READY else None

    index_error = "Previous failure." if status is IndexingStatus.FAILED else None

    return NormativeDocument(
        document_id=DOCUMENT_ID,
        section_id=SECTION_ID,
        category_id=None,
        original_name="GOST.pdf",
        storage_key=(f"{SECTION_ID}/{DOCUMENT_ID}.pdf"),
        mime_type="application/pdf",
        size_bytes=1024,
        sha256="a" * 64,
        index_status=status,
        index_error=index_error,
        indexed_at=indexed_at,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )


def build_use_case(
    *,
    state: FakeState,
    storage: FakeStorage,
    extractor: FakePdfExtractor,
    embedding_provider: FakeEmbeddingProvider,
    vector_store: FakeVectorStore,
    clock: Callable[
        [],
        datetime,
    ],
) -> IndexNormativeDocument:
    """Создаёт indexing use case с fake adapters."""
    return IndexNormativeDocument(
        unit_of_work_factory=build_factory(
            state,
        ),
        storage=storage,
        pdf_extractor=extractor,
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        collection="normative-test",
        chunk_size=100,
        chunk_overlap=10,
        embed_batch_size=2,
        upsert_batch_size=2,
        clock=clock,
    )


@pytest.mark.asyncio
async def test_queued_document_becomes_ready() -> None:
    """Worker выполняет queued -> indexing -> ready."""
    state = FakeState(
        document=make_document(
            status=IndexingStatus.QUEUED,
        ),
    )

    storage = FakeStorage()
    extractor = FakePdfExtractor()
    embedding_provider = FakeEmbeddingProvider()
    vector_store = FakeVectorStore()

    times = iter(
        (
            INDEXING_TIME,
            READY_TIME,
        )
    )

    document = await build_use_case(
        state=state,
        storage=storage,
        extractor=extractor,
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        clock=lambda: next(
            times,
        ),
    ).execute(
        document_id=DOCUMENT_ID,
    )

    assert document.index_status is IndexingStatus.READY
    assert document.ready_for_analysis is True
    assert document.indexed_at == READY_TIME

    assert state.commits == 2
    assert storage.read_count == 1
    assert extractor.call_count == 1

    assert (
        len(
            vector_store.records,
        )
        == 2
    )

    first = vector_store.records[0]

    assert first.point_id == stable_normative_point_id(
        document_id=DOCUMENT_ID,
        page_number=1,
        chunk_index=1,
    )

    assert first.payload == {
        "document_id": str(
            DOCUMENT_ID,
        ),
        "section_id": str(
            SECTION_ID,
        ),
        "category_id": None,
        "source_sha256": "a" * 64,
        "source_file": "GOST.pdf",
        "page": 1,
        "chunk_index": 1,
        "text": "First normative page.",
    }


@pytest.mark.asyncio
async def test_embedding_failure_marks_document_failed() -> None:
    """Ошибка embeddings переводит indexing document в failed."""
    state = FakeState(
        document=make_document(
            status=IndexingStatus.QUEUED,
        ),
    )

    storage = FakeStorage()
    extractor = FakePdfExtractor()
    vector_store = FakeVectorStore()

    times = iter(
        (
            INDEXING_TIME,
            READY_TIME,
        )
    )

    with pytest.raises(
        NormativeIndexingExecutionError,
    ):
        await build_use_case(
            state=state,
            storage=storage,
            extractor=extractor,
            embedding_provider=FakeEmbeddingProvider(
                fail=True,
            ),
            vector_store=vector_store,
            clock=lambda: next(
                times,
            ),
        ).execute(
            document_id=DOCUMENT_ID,
        )

    assert state.document.index_status is IndexingStatus.FAILED

    assert state.document.index_error is not None

    assert "Embedding failure" in state.document.index_error

    assert state.document.ready_for_analysis is False

    assert (
        len(
            vector_store.delete_calls,
        )
        == 2
    )


@pytest.mark.asyncio
async def test_redelivered_ready_task_is_idempotent() -> None:
    """Redelivery завершённого task не выполняет indexing повторно."""
    state = FakeState(
        document=make_document(
            status=IndexingStatus.READY,
        ),
    )

    storage = FakeStorage()
    extractor = FakePdfExtractor()
    embedding_provider = FakeEmbeddingProvider()
    vector_store = FakeVectorStore()

    document = await build_use_case(
        state=state,
        storage=storage,
        extractor=extractor,
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        clock=lambda: READY_TIME,
    ).execute(
        document_id=DOCUMENT_ID,
    )

    assert document.index_status is IndexingStatus.READY
    assert storage.read_count == 0
    assert extractor.call_count == 0
    assert embedding_provider.calls == []
    assert vector_store.records == []
    assert state.commits == 0


@pytest.mark.asyncio
async def test_redelivered_indexing_task_resumes() -> None:
    """Task после worker loss продолжает document в indexing."""
    state = FakeState(
        document=make_document(
            status=IndexingStatus.INDEXING,
        ),
    )

    storage = FakeStorage()
    extractor = FakePdfExtractor()
    embedding_provider = FakeEmbeddingProvider()
    vector_store = FakeVectorStore()

    document = await build_use_case(
        state=state,
        storage=storage,
        extractor=extractor,
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        clock=lambda: READY_TIME,
    ).execute(
        document_id=DOCUMENT_ID,
    )

    assert document.index_status is IndexingStatus.READY
    assert state.commits == 1
    assert (
        len(
            vector_store.records,
        )
        == 2
    )
