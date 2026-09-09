# services/knowledge-service/tests/unit/test_normative_document_management.py

"""Unit tests перемещения и удаления нормативных документов."""

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
from typing import Any
from uuid import UUID

import pytest
from pdrd_knowledge_service.application.ports.vector_store import (
    VectorStoreError,
)
from pdrd_knowledge_service.application.use_cases.normative_documents import (
    DeleteNormativeDocument,
    MoveNormativeDocument,
    NormativeDocumentCategoryError,
    NormativeDocumentMutationConflictError,
)
from pdrd_knowledge_service.domain.normative_catalog import (
    IndexingStatus,
    NormativeCategory,
    NormativeDocument,
)
from pdrd_knowledge_service.transport.http.schemas.normative_documents import (
    MoveNormativeDocumentRequest,
)
from pydantic import ValidationError

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

SECTION_ID = UUID(
    "11111111-1111-1111-1111-111111111111",
)

OTHER_SECTION_ID = UUID(
    "22222222-2222-2222-2222-222222222222",
)

CATEGORY_ID = UUID(
    "33333333-3333-3333-3333-333333333333",
)

OTHER_CATEGORY_ID = UUID(
    "44444444-4444-4444-4444-444444444444",
)

DOCUMENT_ID = UUID(
    "55555555-5555-5555-5555-555555555555",
)


@dataclass
class FakeState:
    """In-memory state document management tests."""

    documents: dict[
        UUID,
        NormativeDocument,
    ] = field(
        default_factory=dict,
    )

    categories: dict[
        UUID,
        NormativeCategory,
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

    async def get_for_update(
        self,
        document_id: UUID,
    ) -> NormativeDocument | None:
        """Эмулирует PostgreSQL locked read."""
        return await self.get(
            document_id,
        )

    async def update(
        self,
        document: NormativeDocument,
    ) -> None:
        """Обновляет document."""
        self._state.documents[document.document_id] = document

    async def delete(
        self,
        document_id: UUID,
    ) -> None:
        """Удаляет document."""
        self._state.documents.pop(
            document_id,
            None,
        )


class FakeCategoryRepository:
    """Fake category repository."""

    def __init__(
        self,
        state: FakeState,
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


class PlaceholderRepository:
    """Неиспользуемый fake repository."""

    pass


class FakeUnitOfWork:
    """Fake Unit of Work management tests."""

    def __init__(
        self,
        state: FakeState,
    ) -> None:
        """Создаёт repositories."""
        self._state = state

        self.sections = PlaceholderRepository()

        self.categories = FakeCategoryRepository(
            state,
        )

        self.documents = FakeDocumentRepository(
            state,
        )

        self.outbox = PlaceholderRepository()

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
    """Fake managed document storage."""

    def __init__(
        self,
    ) -> None:
        """Создаёт fake storage."""
        self.deleted: list[str] = []

    async def delete(
        self,
        *,
        storage_key: str,
    ) -> None:
        """Учитывает physical delete."""
        self.deleted.append(
            storage_key,
        )


class FakeVectorStore:
    """Fake vector store document management."""

    def __init__(
        self,
        *,
        fail_delete: bool = False,
    ) -> None:
        """Сохраняет controlled failure."""
        self._fail_delete = fail_delete

        self.payload_calls: list[
            tuple[
                str,
                str,
                str,
                dict[
                    str,
                    Any,
                ],
            ]
        ] = []

        self.delete_calls: list[
            tuple[
                str,
                str,
                str,
            ]
        ] = []

    async def set_payload_by_filter(
        self,
        *,
        collection: str,
        key: str,
        value: str,
        payload: dict[
            str,
            Any,
        ],
    ) -> None:
        """Учитывает Qdrant payload mutation."""
        self.payload_calls.append(
            (
                collection,
                key,
                value,
                payload,
            )
        )

    async def delete_by_filter(
        self,
        *,
        collection: str,
        key: str,
        value: str,
    ) -> None:
        """Учитывает Qdrant delete или эмулирует failure."""
        self.delete_calls.append(
            (
                collection,
                key,
                value,
            )
        )

        if self._fail_delete:
            raise VectorStoreError(
                "Qdrant unavailable.",
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


def make_category(
    *,
    category_id: UUID = CATEGORY_ID,
    section_id: UUID = SECTION_ID,
) -> NormativeCategory:
    """Создаёт test category."""
    return NormativeCategory(
        category_id=category_id,
        section_id=section_id,
        parent_id=None,
        name="Category",
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )


def make_document(
    *,
    status: IndexingStatus,
    category_id: UUID | None = None,
) -> NormativeDocument:
    """Создаёт document с заданным lifecycle state."""
    index_error = (
        "Previous indexing failure." if status is IndexingStatus.FAILED else None
    )

    indexed_at = BASE_TIME if status is IndexingStatus.READY else None

    return NormativeDocument(
        document_id=DOCUMENT_ID,
        section_id=SECTION_ID,
        category_id=category_id,
        original_name="GOST.pdf",
        storage_key=f"{SECTION_ID}/{DOCUMENT_ID}.pdf",
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
async def test_uploaded_document_moves_without_qdrant_update() -> None:
    """Неиндексированный document перемещается только в PostgreSQL."""
    state = FakeState()

    state.documents[DOCUMENT_ID] = make_document(
        status=IndexingStatus.UPLOADED,
    )

    state.categories[CATEGORY_ID] = make_category()

    vector_store = FakeVectorStore()

    document = await MoveNormativeDocument(
        unit_of_work_factory=build_factory(
            state,
        ),
        vector_store=vector_store,
        collection="normative-test",
        clock=lambda: CHANGED_TIME,
    ).execute(
        document_id=DOCUMENT_ID,
        category_id=CATEGORY_ID,
    )

    assert document.category_id == CATEGORY_ID
    assert state.documents[DOCUMENT_ID].category_id == CATEGORY_ID
    assert state.commits == 1
    assert vector_store.payload_calls == []


@pytest.mark.asyncio
async def test_ready_document_move_updates_qdrant_payload() -> None:
    """READY document синхронно меняет denormalized category_id в Qdrant."""
    state = FakeState()

    state.documents[DOCUMENT_ID] = make_document(
        status=IndexingStatus.READY,
    )

    state.categories[CATEGORY_ID] = make_category()

    vector_store = FakeVectorStore()

    document = await MoveNormativeDocument(
        unit_of_work_factory=build_factory(
            state,
        ),
        vector_store=vector_store,
        collection="normative-test",
        clock=lambda: CHANGED_TIME,
    ).execute(
        document_id=DOCUMENT_ID,
        category_id=CATEGORY_ID,
    )

    assert document.category_id == CATEGORY_ID

    assert vector_store.payload_calls == [
        (
            "normative-test",
            "document_id",
            str(
                DOCUMENT_ID,
            ),
            {
                "category_id": str(
                    CATEGORY_ID,
                )
            },
        )
    ]


@pytest.mark.asyncio
async def test_document_cannot_move_to_foreign_section_category() -> None:
    """Document нельзя переместить в category другого section."""
    state = FakeState()

    state.documents[DOCUMENT_ID] = make_document(
        status=IndexingStatus.UPLOADED,
    )

    state.categories[OTHER_CATEGORY_ID] = make_category(
        category_id=OTHER_CATEGORY_ID,
        section_id=OTHER_SECTION_ID,
    )

    with pytest.raises(
        NormativeDocumentCategoryError,
    ):
        await MoveNormativeDocument(
            unit_of_work_factory=build_factory(
                state,
            ),
            vector_store=FakeVectorStore(),
            collection="normative-test",
            clock=lambda: CHANGED_TIME,
        ).execute(
            document_id=DOCUMENT_ID,
            category_id=OTHER_CATEGORY_ID,
        )

    assert state.documents[DOCUMENT_ID].category_id is None
    assert state.commits == 0


@pytest.mark.asyncio
async def test_queued_document_cannot_be_moved() -> None:
    """Queued document нельзя менять параллельно indexing worker."""
    state = FakeState()

    state.documents[DOCUMENT_ID] = make_document(
        status=IndexingStatus.QUEUED,
    )

    state.categories[CATEGORY_ID] = make_category()

    with pytest.raises(
        NormativeDocumentMutationConflictError,
    ):
        await MoveNormativeDocument(
            unit_of_work_factory=build_factory(
                state,
            ),
            vector_store=FakeVectorStore(),
            collection="normative-test",
            clock=lambda: CHANGED_TIME,
        ).execute(
            document_id=DOCUMENT_ID,
            category_id=CATEGORY_ID,
        )

    assert state.commits == 0


@pytest.mark.asyncio
async def test_ready_document_delete_removes_all_managed_state() -> None:
    """DELETE удаляет Qdrant, physical file и PostgreSQL metadata."""
    state = FakeState()

    document = make_document(
        status=IndexingStatus.READY,
    )

    state.documents[DOCUMENT_ID] = document

    storage = FakeStorage()
    vector_store = FakeVectorStore()

    deleted_id = await DeleteNormativeDocument(
        unit_of_work_factory=build_factory(
            state,
        ),
        storage=storage,
        vector_store=vector_store,
        collection="normative-test",
        clock=lambda: CHANGED_TIME,
    ).execute(
        document_id=DOCUMENT_ID,
    )

    assert deleted_id == DOCUMENT_ID
    assert DOCUMENT_ID not in state.documents
    assert state.commits == 2

    assert vector_store.delete_calls == [
        (
            "normative-test",
            "document_id",
            str(
                DOCUMENT_ID,
            ),
        )
    ]

    assert storage.deleted == [
        document.storage_key,
    ]


@pytest.mark.asyncio
async def test_queued_document_cannot_be_deleted() -> None:
    """Queued document нельзя удалить до завершения worker."""
    state = FakeState()

    state.documents[DOCUMENT_ID] = make_document(
        status=IndexingStatus.QUEUED,
    )

    storage = FakeStorage()
    vector_store = FakeVectorStore()

    with pytest.raises(
        NormativeDocumentMutationConflictError,
    ):
        await DeleteNormativeDocument(
            unit_of_work_factory=build_factory(
                state,
            ),
            storage=storage,
            vector_store=vector_store,
            collection="normative-test",
            clock=lambda: CHANGED_TIME,
        ).execute(
            document_id=DOCUMENT_ID,
        )

    assert vector_store.delete_calls == []
    assert storage.deleted == []
    assert state.commits == 0


@pytest.mark.asyncio
async def test_missing_document_delete_is_idempotent() -> None:
    """Повторный DELETE уже удалённого document считается успешным."""
    state = FakeState()

    storage = FakeStorage()
    vector_store = FakeVectorStore()

    deleted_id = await DeleteNormativeDocument(
        unit_of_work_factory=build_factory(
            state,
        ),
        storage=storage,
        vector_store=vector_store,
        collection="normative-test",
        clock=lambda: CHANGED_TIME,
    ).execute(
        document_id=DOCUMENT_ID,
    )

    assert deleted_id == DOCUMENT_ID
    assert vector_store.delete_calls == []
    assert storage.deleted == []
    assert state.commits == 0


@pytest.mark.asyncio
async def test_qdrant_failure_leaves_document_deleting_for_retry() -> None:
    """Qdrant failure оставляет durable deleting state для повторного DELETE."""
    state = FakeState()

    document = make_document(
        status=IndexingStatus.READY,
    )

    state.documents[DOCUMENT_ID] = document

    storage = FakeStorage()

    with pytest.raises(
        VectorStoreError,
        match="Qdrant unavailable",
    ):
        await DeleteNormativeDocument(
            unit_of_work_factory=build_factory(
                state,
            ),
            storage=storage,
            vector_store=FakeVectorStore(
                fail_delete=True,
            ),
            collection="normative-test",
            clock=lambda: CHANGED_TIME,
        ).execute(
            document_id=DOCUMENT_ID,
        )

    assert state.documents[DOCUMENT_ID].index_status is IndexingStatus.DELETING

    assert storage.deleted == []
    assert state.commits == 1


def test_move_request_requires_explicit_category_id() -> None:
    """PATCH различает отсутствие поля и явный перенос в root."""
    with pytest.raises(
        ValidationError,
    ):
        MoveNormativeDocumentRequest.model_validate(
            {},
        )

    request = MoveNormativeDocumentRequest.model_validate(
        {
            "category_id": None,
        }
    )

    assert request.category_id is None
