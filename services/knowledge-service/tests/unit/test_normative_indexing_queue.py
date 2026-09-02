# services/knowledge-service/tests/unit/test_normative_indexing_queue.py

"""Unit tests durable постановки нормативного документа на индексацию."""

from collections.abc import Callable
from dataclasses import (
    dataclass,
    field,
)
from datetime import (
    UTC,
    datetime,
    timedelta,
)
from types import TracebackType
from uuid import UUID

import pytest
from pdrd_knowledge_service.application.use_cases.normative_documents import (
    NormativeDocumentNotFoundError,
)
from pdrd_knowledge_service.application.use_cases.normative_indexing_queue import (
    NormativeDocumentIndexingConflictError,
    QueueNormativeDocument,
)
from pdrd_knowledge_service.domain.normative_catalog import (
    IndexingStatus,
    NormativeDocument,
)
from pdrd_knowledge_service.domain.normative_outbox import (
    NormativeOutboxMessage,
)

BASE_TIME = datetime(
    2026,
    9,
    2,
    12,
    0,
    tzinfo=UTC,
)

CHANGED_TIME = BASE_TIME + timedelta(
    minutes=1,
)

DOCUMENT_ID = UUID(
    "55555555-5555-5555-5555-555555555555",
)

SECTION_ID = UUID(
    "11111111-1111-1111-1111-111111111111",
)

MESSAGE_ID = UUID(
    "66666666-6666-6666-6666-666666666666",
)


@dataclass
class FakeState:
    """In-memory state queue use case."""

    documents: dict[
        UUID,
        NormativeDocument,
    ] = field(
        default_factory=dict,
    )

    outbox: dict[
        UUID,
        NormativeOutboxMessage,
    ] = field(
        default_factory=dict,
    )

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
        """Возвращает document."""
        return self._state.documents.get(
            document_id,
        )

    async def update(
        self,
        document: NormativeDocument,
    ) -> None:
        """Обновляет document."""
        self._state.documents[document.document_id] = document


class FakeOutboxRepository:
    """Fake outbox repository."""

    def __init__(
        self,
        state: FakeState,
    ) -> None:
        """Сохраняет state."""
        self._state = state

    async def add(
        self,
        message: NormativeOutboxMessage,
    ) -> None:
        """Добавляет outbox message."""
        self._state.outbox[message.message_id] = message


class PlaceholderRepository:
    """Неиспользуемый repository fake Unit of Work."""

    pass


class FakeUnitOfWork:
    """Fake Unit of Work queue use case."""

    def __init__(
        self,
        state: FakeState,
    ) -> None:
        """Создаёт fake repositories."""
        self._state = state

        self.sections = PlaceholderRepository()

        self.categories = PlaceholderRepository()

        self.documents = FakeDocumentRepository(
            state,
        )

        self.outbox = FakeOutboxRepository(
            state,
        )

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
    """Создаёт document в указанном indexing status."""
    index_error = "Test indexing failure." if status is IndexingStatus.FAILED else None

    indexed_at = BASE_TIME if status is IndexingStatus.READY else None

    return NormativeDocument(
        document_id=DOCUMENT_ID,
        section_id=SECTION_ID,
        category_id=None,
        original_name="test.pdf",
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


@pytest.mark.asyncio
async def test_uploaded_document_is_queued_with_outbox() -> None:
    """Uploaded document и outbox сохраняются одной transaction."""
    state = FakeState()

    state.documents[DOCUMENT_ID] = make_document(
        status=IndexingStatus.UPLOADED,
    )

    use_case = QueueNormativeDocument(
        unit_of_work_factory=build_factory(
            state,
        ),
        clock=lambda: CHANGED_TIME,
        identifier_factory=lambda: MESSAGE_ID,
    )

    document = await use_case.execute(
        document_id=DOCUMENT_ID,
    )

    message = state.outbox[MESSAGE_ID]

    assert document.index_status is IndexingStatus.QUEUED
    assert document.updated_at == CHANGED_TIME
    assert document.ready_for_analysis is False

    assert message.aggregate_id == DOCUMENT_ID
    assert message.event_type == (NormativeOutboxMessage.INDEX_REQUESTED_EVENT)
    assert message.payload == {
        "document_id": str(
            DOCUMENT_ID,
        ),
    }

    assert message.published_at is None
    assert state.commits == 1


@pytest.mark.asyncio
async def test_failed_document_can_be_requeued() -> None:
    """Failed document можно поставить на повторную индексацию."""
    state = FakeState()

    state.documents[DOCUMENT_ID] = make_document(
        status=IndexingStatus.FAILED,
    )

    document = await QueueNormativeDocument(
        unit_of_work_factory=build_factory(
            state,
        ),
        clock=lambda: CHANGED_TIME,
        identifier_factory=lambda: MESSAGE_ID,
    ).execute(
        document_id=DOCUMENT_ID,
    )

    assert document.index_status is IndexingStatus.QUEUED
    assert document.index_error is None
    assert document.indexed_at is None


@pytest.mark.asyncio
async def test_ready_document_can_be_requeued() -> None:
    """Ready document можно отправить на reindex."""
    state = FakeState()

    state.documents[DOCUMENT_ID] = make_document(
        status=IndexingStatus.READY,
    )

    document = await QueueNormativeDocument(
        unit_of_work_factory=build_factory(
            state,
        ),
        clock=lambda: CHANGED_TIME,
        identifier_factory=lambda: MESSAGE_ID,
    ).execute(
        document_id=DOCUMENT_ID,
    )

    assert document.index_status is IndexingStatus.QUEUED
    assert document.indexed_at is None
    assert document.ready_for_analysis is False


@pytest.mark.asyncio
async def test_queued_document_cannot_be_queued_twice() -> None:
    """Повторная постановка queued документа не создаёт duplicate event."""
    state = FakeState()

    state.documents[DOCUMENT_ID] = make_document(
        status=IndexingStatus.QUEUED,
    )

    with pytest.raises(
        NormativeDocumentIndexingConflictError,
    ):
        await QueueNormativeDocument(
            unit_of_work_factory=build_factory(
                state,
            ),
            clock=lambda: CHANGED_TIME,
            identifier_factory=lambda: MESSAGE_ID,
        ).execute(
            document_id=DOCUMENT_ID,
        )

    assert state.outbox == {}
    assert state.commits == 0


@pytest.mark.asyncio
async def test_missing_document_cannot_be_queued() -> None:
    """Неизвестный document возвращает application not-found."""
    state = FakeState()

    with pytest.raises(
        NormativeDocumentNotFoundError,
    ):
        await QueueNormativeDocument(
            unit_of_work_factory=build_factory(
                state,
            ),
            clock=lambda: CHANGED_TIME,
            identifier_factory=lambda: MESSAGE_ID,
        ).execute(
            document_id=DOCUMENT_ID,
        )

    assert state.outbox == {}
    assert state.commits == 0
