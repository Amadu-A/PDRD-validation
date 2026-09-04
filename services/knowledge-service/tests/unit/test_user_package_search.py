# services/knowledge-service/tests/unit/test_user_package_search.py

"""Unit tests semantic retrieval пользовательских пакетов."""

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
    NormativeSearchScopeError,
    SearchNormative,
)
from pdrd_knowledge_service.application.use_cases.user_packages import (
    SearchUserPackages,
)
from pdrd_knowledge_service.domain.normative_catalog import (
    CatalogArea,
    IndexingStatus,
    NormativeDocument,
    NormativeSection,
)
from pdrd_knowledge_service.domain.search import (
    VectorPoint,
)

SECTION_ID = UUID(
    "11111111-1111-1111-1111-111111111111",
)

NORMATIVE_ID = UUID(
    "22222222-2222-2222-2222-222222222222",
)

PACKAGE_ID = UUID(
    "33333333-3333-3333-3333-333333333333",
)

NOW = datetime(
    2026,
    9,
    4,
    12,
    0,
    tzinfo=UTC,
)


@dataclass
class FakeState:
    """Fake managed catalog state."""

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


class FakeSections:
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
        return self._state.sections.get(
            section_id,
        )

    async def list_all(
        self,
    ) -> list[NormativeSection]:
        """Возвращает все sections."""
        return list(self._state.sections.values())


class FakeDocuments:
    """Fake document repository."""

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
        """Возвращает найденные документы."""
        return [
            self._state.documents[document_id]
            for document_id in document_ids
            if document_id in self._state.documents
        ]

    async def list_by_section(
        self,
        section_id: UUID,
    ) -> list[NormativeDocument]:
        """Возвращает документы section."""
        return [
            document
            for document in self._state.documents.values()
            if document.section_id == section_id
        ]


class FakeUnitOfWork:
    """Fake catalog UoW."""

    def __init__(
        self,
        state: FakeState,
    ) -> None:
        """Создаёт repositories."""
        self.sections = FakeSections(
            state,
        )

        self.documents = FakeDocuments(
            state,
        )

    async def __aenter__(
        self,
    ) -> "FakeUnitOfWork":
        """Открывает fake UoW."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Закрывает fake UoW."""
        del (
            exc_type,
            exc_value,
            traceback,
        )


class FakeEmbedding:
    """Fake embedding provider."""

    async def embed(
        self,
        texts: tuple[
            str,
            ...,
        ],
        *,
        instruction: str | None,
    ) -> list[list[float]]:
        """Возвращает deterministic vectors."""
        assert texts

        assert instruction is not None

        return [
            [
                1.0,
                0.0,
            ]
            for _ in texts
        ]


class FakeVectorStore:
    """Fake vector store с сохранением filter."""

    def __init__(
        self,
    ) -> None:
        """Подготавливает state."""
        self.filters = []

        self.unfiltered_calls = 0

    async def search(
        self,
        *,
        collection: str,
        vector: list[float],
        limit: int,
    ) -> list[VectorPoint]:
        """Считает unfiltered calls."""
        del (
            collection,
            vector,
            limit,
        )

        self.unfiltered_calls += 1

        return []

    async def search_filtered(
        self,
        *,
        collection: str,
        vector: list[float],
        limit: int,
        search_filter: object,
    ) -> list[VectorPoint]:
        """Возвращает один package point."""
        del (
            collection,
            vector,
            limit,
        )

        self.filters.append(
            search_filter,
        )

        return [
            VectorPoint(
                point_id="package-point",
                score=0.91,
                payload={
                    "document_id": str(
                        PACKAGE_ID,
                    ),
                    "section_id": str(
                        SECTION_ID,
                    ),
                    "category_id": None,
                    "source_sha256": "a" * 64,
                    "source_file": "requirements.pdf",
                    "page": 7,
                    "chunk_index": 1,
                    "text": "Требование заказчика.",
                },
            )
        ]


def make_section() -> NormativeSection:
    """Создаёт section."""
    return NormativeSection(
        section_id=SECTION_ID,
        name="КИПиА",
        system_prompt="Prompt.",
        created_at=NOW,
        updated_at=NOW,
    )


def make_document(
    *,
    document_id: UUID,
    area: CatalogArea,
) -> NormativeDocument:
    """Создаёт READY managed document."""
    return NormativeDocument(
        document_id=document_id,
        section_id=SECTION_ID,
        category_id=None,
        original_name=f"{document_id}.pdf",
        storage_key=(f"{SECTION_ID}/{document_id}.pdf"),
        mime_type="application/pdf",
        size_bytes=100,
        sha256="a" * 64,
        index_status=IndexingStatus.READY,
        index_error=None,
        indexed_at=NOW,
        created_at=NOW,
        updated_at=NOW,
        area=area,
    )


def build_search(
    state: FakeState,
    vector_store: FakeVectorStore,
) -> SearchUserPackages:
    """Собирает test use case."""

    def uow_factory() -> FakeUnitOfWork:
        """Создаёт новый fake UoW."""
        return FakeUnitOfWork(
            state,
        )

    return SearchUserPackages(
        managed_search=SearchNormative(
            embedding_provider=FakeEmbedding(),
            vector_store=vector_store,  # type: ignore[arg-type]
            collection="test",
            embedding_model="embed-test",
            top_k=5,
            max_sources=5,
            unit_of_work_factory=uow_factory,  # type: ignore[arg-type]
        )
    )


@pytest.mark.asyncio
async def test_user_package_search_returns_u_sources() -> None:
    """Package retrieval использует U-prefix и exact package ID."""
    state = FakeState(
        sections={
            SECTION_ID: make_section(),
        },
        documents={
            PACKAGE_ID: make_document(
                document_id=PACKAGE_ID,
                area=CatalogArea.USER_PACKAGE,
            ),
        },
    )

    vector_store = FakeVectorStore()

    result = await build_search(
        state,
        vector_store,
    ).execute(
        [
            "IP54 шкаф",
        ],
        section_id=SECTION_ID,
        document_ids=[
            PACKAGE_ID,
        ],
    )

    assert (
        len(
            result.sources,
        )
        == 1
    )

    source = result.sources[0]

    assert source.source_id == "U1"

    assert source.document_id == str(
        PACKAGE_ID,
    )

    assert vector_store.unfiltered_calls == 0

    assert (
        len(
            vector_store.filters,
        )
        == 1
    )


@pytest.mark.asyncio
async def test_package_search_rejects_normative_document() -> None:
    """Package endpoint нельзя использовать с normative UUID."""
    state = FakeState(
        sections={
            SECTION_ID: make_section(),
        },
        documents={
            NORMATIVE_ID: make_document(
                document_id=NORMATIVE_ID,
                area=CatalogArea.NORMATIVE,
            ),
        },
    )

    vector_store = FakeVectorStore()

    with pytest.raises(
        NormativeSearchScopeError,
        match="другой области",
    ):
        await build_search(
            state,
            vector_store,
        ).execute(
            [
                "кабель",
            ],
            section_id=SECTION_ID,
            document_ids=[
                NORMATIVE_ID,
            ],
        )

    assert vector_store.unfiltered_calls == 0

    assert vector_store.filters == []


@pytest.mark.asyncio
async def test_empty_package_scope_does_not_search_qdrant() -> None:
    """Пустой selected package list не выполняет vector query."""
    state = FakeState(
        sections={
            SECTION_ID: make_section(),
        },
    )

    vector_store = FakeVectorStore()

    result = await build_search(
        state,
        vector_store,
    ).execute(
        [
            "шкаф",
        ],
        section_id=SECTION_ID,
        document_ids=[],
    )

    assert result.sources == ()

    assert vector_store.unfiltered_calls == 0

    assert vector_store.filters == []


@pytest.mark.asyncio
async def test_absent_package_scope_returns_empty() -> None:
    """Legacy analysis без packages не видит package collection."""
    state = FakeState()

    vector_store = FakeVectorStore()

    result = await build_search(
        state,
        vector_store,
    ).execute(
        [
            "шкаф",
        ],
        section_id=None,
        document_ids=None,
    )

    assert result.sources == ()

    assert vector_store.unfiltered_calls == 0

    assert vector_store.filters == []
