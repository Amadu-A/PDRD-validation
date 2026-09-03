# services/knowledge-service/tests/unit/test_normative_search.py

"""Unit-тесты нормативного retrieval."""

from collections.abc import Callable
from dataclasses import (
    dataclass,
    field,
)
from datetime import (
    UTC,
    datetime,
)
from types import TracebackType
from uuid import UUID

import pytest
from pdrd_knowledge_service.application.use_cases.normative import (
    NORMATIVE_QUERY_INSTRUCTION,
    NormativeSearchScopeConflictError,
    NormativeSearchScopeError,
    SearchNormative,
)
from pdrd_knowledge_service.domain.normative_catalog import (
    IndexingStatus,
    NormativeDocument,
    NormativeSection,
)
from pdrd_knowledge_service.domain.search import (
    VectorPoint,
    VectorSearchFilter,
)
from pdrd_knowledge_service.transport.http.schemas.search import (
    NormativeSearchRequest,
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

SECTION_ID = UUID(
    "11111111-1111-1111-1111-111111111111",
)

OTHER_SECTION_ID = UUID(
    "22222222-2222-2222-2222-222222222222",
)

DOCUMENT_A_ID = UUID(
    "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
)

DOCUMENT_B_ID = UUID(
    "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
)

MISSING_DOCUMENT_ID = UUID(
    "cccccccc-cccc-cccc-cccc-cccccccccccc",
)


class FakeEmbeddingProvider:
    """Fake embedding provider для application tests."""

    def __init__(
        self,
    ) -> None:
        """Инициализирует captured calls."""
        self.texts: tuple[str, ...] = ()

        self.instruction = ""

    async def embed(
        self,
        texts: tuple[str, ...],
        *,
        instruction: str,
    ) -> list[list[float]]:
        """Возвращает deterministic vectors."""
        self.texts = texts
        self.instruction = instruction

        return [
            [
                float(
                    index,
                ),
            ]
            for index in range(
                1,
                len(
                    texts,
                )
                + 1,
            )
        ]

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает fake readiness."""
        return True


class FakeVectorStore:
    """Fake vector storage для normative tests."""

    def __init__(
        self,
    ) -> None:
        """Создаёт captured scoped filters."""
        self.filtered_calls: list[VectorSearchFilter] = []

    async def search(
        self,
        *,
        collection: str,
        vector: list[float],
        limit: int,
    ) -> list[VectorPoint]:
        """Возвращает legacy unscoped результаты."""
        assert collection == "normative"
        assert limit == 4

        if vector == [
            1.0,
        ]:
            return [
                VectorPoint(
                    point_id="shared",
                    score=0.7,
                    payload={
                        "source_file": "old.pdf",
                        "page": 1,
                        "text": "Older duplicate",
                    },
                ),
                VectorPoint(
                    point_id="unique",
                    score=0.8,
                    payload={
                        "source_file": "unique.pdf",
                        "page": 3,
                        "chunk_index": 2,
                        "text": "Unique requirement",
                    },
                ),
            ]

        return [
            VectorPoint(
                point_id="shared",
                score=0.9,
                payload={
                    "source_file": "better.pdf",
                    "source_path": "/norms/better.pdf",
                    "page": 7,
                    "chunk_index": 4,
                    "text": "Better duplicate",
                },
            )
        ]

    async def search_filtered(
        self,
        *,
        collection: str,
        vector: list[float],
        limit: int,
        search_filter: VectorSearchFilter,
    ) -> list[VectorPoint]:
        """Возвращает managed point и сохраняет scope filter."""
        assert collection == "normative"
        assert vector
        assert limit == 4

        self.filtered_calls.append(
            search_filter,
        )

        return [
            VectorPoint(
                point_id="managed-point",
                score=0.95,
                payload={
                    "document_id": str(
                        DOCUMENT_A_ID,
                    ),
                    "section_id": str(
                        SECTION_ID,
                    ),
                    "category_id": None,
                    "source_sha256": "a" * 64,
                    "source_file": "GOST.pdf",
                    "page": 12,
                    "chunk_index": 2,
                    "text": "Managed normative requirement.",
                },
            )
        ]

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает fake readiness."""
        return True

    async def collection_exists(
        self,
        collection: str,
    ) -> bool:
        """Возвращает fake collection existence."""
        return bool(
            collection,
        )


@dataclass
class FakeState:
    """In-memory catalog state scoped retrieval tests."""

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
    """Fake sections repository."""

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
        return self._state.sections.get(
            section_id,
        )


class FakeDocumentRepository:
    """Fake documents repository."""

    def __init__(
        self,
        state: FakeState,
    ) -> None:
        """Сохраняет state."""
        self._state = state

    async def list_by_ids(
        self,
        document_ids: tuple[
            UUID,
            ...,
        ],
    ) -> list[NormativeDocument]:
        """Возвращает существующие selected documents."""
        return [
            self._state.documents[document_id]
            for document_id in document_ids
            if document_id in self._state.documents
        ]


class PlaceholderRepository:
    """Неиспользуемый fake repository."""

    pass


class FakeUnitOfWork:
    """Fake catalog Unit of Work scoped retrieval."""

    def __init__(
        self,
        state: FakeState,
    ) -> None:
        """Создаёт repositories."""
        self.sections = FakeSectionRepository(
            state,
        )

        self.documents = FakeDocumentRepository(
            state,
        )

        self.categories = PlaceholderRepository()
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
        """Fake commit."""
        return None

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


def make_section(
    *,
    section_id: UUID = SECTION_ID,
) -> NormativeSection:
    """Создаёт test section."""
    return NormativeSection(
        section_id=section_id,
        name="ЭОМ",
        system_prompt="Проверяй требования.",
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )


def make_document(
    *,
    document_id: UUID,
    section_id: UUID = SECTION_ID,
    status: IndexingStatus = IndexingStatus.READY,
) -> NormativeDocument:
    """Создаёт managed test document."""
    indexed_at = BASE_TIME if status is IndexingStatus.READY else None

    index_error = "Previous error." if status is IndexingStatus.FAILED else None

    return NormativeDocument(
        document_id=document_id,
        section_id=section_id,
        category_id=None,
        original_name="GOST.pdf",
        storage_key=f"{section_id}/{document_id}.pdf",
        mime_type="application/pdf",
        size_bytes=1024,
        sha256="a" * 64,
        index_status=status,
        index_error=index_error,
        indexed_at=indexed_at,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )


async def test_normative_search_deduplicates_queries_and_points() -> None:
    """Legacy path сохраняет deduplication запросов и point ids."""
    embedding = FakeEmbeddingProvider()

    use_case = SearchNormative(
        embedding_provider=embedding,
        vector_store=FakeVectorStore(),
        collection="normative",
        embedding_model="test-embedding",
        top_k=4,
        max_sources=12,
    )

    result = await use_case.execute(
        [
            " cable routing ",
            "cable routing",
            "",
            "grounding",
        ]
    )

    assert result.queries == (
        "cable routing",
        "grounding",
    )

    assert embedding.texts == result.queries
    assert embedding.instruction == NORMATIVE_QUERY_INSTRUCTION

    assert (
        len(
            result.sources,
        )
        == 2
    )

    first = result.sources[0]

    second = result.sources[1]

    assert first.source_id == "N1"
    assert first.point_id == "shared"
    assert first.score == 0.9
    assert first.source_file == "better.pdf"
    assert first.page == 7

    assert second.source_id == "N2"
    assert second.point_id == "unique"
    assert second.score == 0.8


async def test_empty_normative_search_does_not_call_embedding() -> None:
    """Проверяет быстрый ответ для пустого набора тем."""
    embedding = FakeEmbeddingProvider()

    use_case = SearchNormative(
        embedding_provider=embedding,
        vector_store=FakeVectorStore(),
        collection="normative",
        embedding_model="test-embedding",
        top_k=4,
        max_sources=12,
    )

    result = await use_case.execute(
        [
            "",
            "   ",
        ]
    )

    assert result.queries == ()
    assert result.sources == ()

    assert embedding.texts == ()


async def test_scoped_search_uses_exact_section_and_document_filter() -> None:
    """Scoped retrieval передаёт Qdrant только разрешённые document IDs."""
    state = FakeState()

    state.sections[SECTION_ID] = make_section()

    state.documents[DOCUMENT_A_ID] = make_document(
        document_id=DOCUMENT_A_ID,
    )

    state.documents[DOCUMENT_B_ID] = make_document(
        document_id=DOCUMENT_B_ID,
    )

    embedding = FakeEmbeddingProvider()
    vector_store = FakeVectorStore()

    result = await SearchNormative(
        embedding_provider=embedding,
        vector_store=vector_store,
        collection="normative",
        embedding_model="test-embedding",
        top_k=4,
        max_sources=12,
        unit_of_work_factory=build_factory(
            state,
        ),
    ).execute(
        [
            "grounding",
        ],
        section_id=SECTION_ID,
        document_ids=[
            DOCUMENT_A_ID,
            DOCUMENT_B_ID,
            DOCUMENT_A_ID,
        ],
    )

    assert (
        len(
            vector_store.filtered_calls,
        )
        == 1
    )

    search_filter = vector_store.filtered_calls[0]

    assert search_filter.must[0].key == "section_id"

    assert search_filter.must[0].values == (
        str(
            SECTION_ID,
        ),
    )

    assert search_filter.must[1].key == "document_id"

    assert search_filter.must[1].values == (
        str(
            DOCUMENT_A_ID,
        ),
        str(
            DOCUMENT_B_ID,
        ),
    )

    source = result.sources[0]

    assert source.document_id == str(
        DOCUMENT_A_ID,
    )

    assert source.section_id == str(
        SECTION_ID,
    )

    assert source.source_sha256 == "a" * 64


async def test_scoped_search_rejects_foreign_section_document() -> None:
    """Document другого section не может попасть в retrieval scope."""
    state = FakeState()

    state.sections[SECTION_ID] = make_section()

    state.documents[DOCUMENT_A_ID] = make_document(
        document_id=DOCUMENT_A_ID,
        section_id=OTHER_SECTION_ID,
    )

    embedding = FakeEmbeddingProvider()
    vector_store = FakeVectorStore()

    with pytest.raises(
        NormativeSearchScopeError,
        match="другому нормативному разделу",
    ):
        await SearchNormative(
            embedding_provider=embedding,
            vector_store=vector_store,
            collection="normative",
            embedding_model="test-embedding",
            top_k=4,
            max_sources=12,
            unit_of_work_factory=build_factory(
                state,
            ),
        ).execute(
            [
                "grounding",
            ],
            section_id=SECTION_ID,
            document_ids=[
                DOCUMENT_A_ID,
            ],
        )

    assert embedding.texts == ()
    assert vector_store.filtered_calls == []


async def test_scoped_search_rejects_non_ready_document() -> None:
    """Только READY document разрешён для scoped retrieval."""
    state = FakeState()

    state.sections[SECTION_ID] = make_section()

    state.documents[DOCUMENT_A_ID] = make_document(
        document_id=DOCUMENT_A_ID,
        status=IndexingStatus.UPLOADED,
    )

    embedding = FakeEmbeddingProvider()
    vector_store = FakeVectorStore()

    with pytest.raises(
        NormativeSearchScopeConflictError,
        match="не готовы",
    ):
        await SearchNormative(
            embedding_provider=embedding,
            vector_store=vector_store,
            collection="normative",
            embedding_model="test-embedding",
            top_k=4,
            max_sources=12,
            unit_of_work_factory=build_factory(
                state,
            ),
        ).execute(
            [
                "grounding",
            ],
            section_id=SECTION_ID,
            document_ids=[
                DOCUMENT_A_ID,
            ],
        )

    assert embedding.texts == ()
    assert vector_store.filtered_calls == []


async def test_scoped_search_rejects_missing_document() -> None:
    """Удалённый/stale document ID не допускается в scope."""
    state = FakeState()

    state.sections[SECTION_ID] = make_section()

    with pytest.raises(
        NormativeSearchScopeError,
        match="не найдены",
    ):
        await SearchNormative(
            embedding_provider=FakeEmbeddingProvider(),
            vector_store=FakeVectorStore(),
            collection="normative",
            embedding_model="test-embedding",
            top_k=4,
            max_sources=12,
            unit_of_work_factory=build_factory(
                state,
            ),
        ).execute(
            [
                "grounding",
            ],
            section_id=SECTION_ID,
            document_ids=[
                MISSING_DOCUMENT_ID,
            ],
        )


async def test_empty_document_selection_skips_embedding_and_qdrant() -> None:
    """Clear all documents означает нормативный retrieval без sources."""
    state = FakeState()

    state.sections[SECTION_ID] = make_section()

    embedding = FakeEmbeddingProvider()
    vector_store = FakeVectorStore()

    result = await SearchNormative(
        embedding_provider=embedding,
        vector_store=vector_store,
        collection="normative",
        embedding_model="test-embedding",
        top_k=4,
        max_sources=12,
        unit_of_work_factory=build_factory(
            state,
        ),
    ).execute(
        [
            "grounding",
        ],
        section_id=SECTION_ID,
        document_ids=[],
    )

    assert result.queries == ("grounding",)

    assert result.sources == ()
    assert embedding.texts == ()
    assert vector_store.filtered_calls == []


def test_normative_search_request_requires_scope_pair() -> None:
    """HTTP request не принимает половину managed scope."""
    with pytest.raises(
        ValidationError,
    ):
        NormativeSearchRequest.model_validate(
            {
                "queries": [
                    "grounding",
                ],
                "section_id": str(
                    SECTION_ID,
                ),
            }
        )

    request = NormativeSearchRequest.model_validate(
        {
            "queries": [
                "grounding",
            ],
            "section_id": str(
                SECTION_ID,
            ),
            "document_ids": [
                str(
                    DOCUMENT_A_ID,
                ),
            ],
        }
    )

    assert request.section_id == SECTION_ID

    assert request.document_ids == [
        DOCUMENT_A_ID,
    ]
