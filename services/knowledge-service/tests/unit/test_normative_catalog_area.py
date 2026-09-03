# services/knowledge-service/tests/unit/test_normative_catalog_area.py

"""Unit tests разделения managed catalog на области."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import (
    UTC,
    datetime,
    timedelta,
)
from types import TracebackType
from uuid import UUID

import pytest
from pdrd_knowledge_service.application.use_cases.normative_categories import (
    CreateNormativeCategory,
    ListNormativeCategories,
    NormativeCategoryParentError,
)
from pdrd_knowledge_service.application.use_cases.normative_documents import (
    ListNormativeDocuments,
    MoveNormativeDocument,
    NormativeDocumentCategoryError,
)
from pdrd_knowledge_service.domain.normative_catalog import (
    CatalogArea,
    IndexingStatus,
    NormativeCategory,
    NormativeDocument,
    NormativeSection,
)

BASE_TIME = datetime(
    2026,
    9,
    3,
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

NORMATIVE_CATEGORY_ID = UUID(
    "22222222-2222-2222-2222-222222222222",
)

PACKAGE_CATEGORY_ID = UUID(
    "33333333-3333-3333-3333-333333333333",
)

NEW_CATEGORY_ID = UUID(
    "44444444-4444-4444-4444-444444444444",
)

NORMATIVE_DOCUMENT_ID = UUID(
    "55555555-5555-5555-5555-555555555555",
)

PACKAGE_DOCUMENT_ID = UUID(
    "66666666-6666-6666-6666-666666666666",
)


@dataclass
class FakeState:
    """In-memory catalog state."""

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


class FakeSectionRepository:
    """Fake sections."""

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


class FakeCategoryRepository:
    """Fake categories."""

    def __init__(
        self,
        state: FakeState,
    ) -> None:
        """Сохраняет state."""
        self._state = state

    async def add(
        self,
        category: NormativeCategory,
    ) -> None:
        """Добавляет category."""
        self._state.categories[category.category_id] = category

    async def get(
        self,
        category_id: UUID,
    ) -> NormativeCategory | None:
        """Возвращает category."""
        return self._state.categories.get(
            category_id,
        )

    async def list_by_section(
        self,
        section_id: UUID,
    ) -> list[NormativeCategory]:
        """Возвращает categories section."""
        return [
            category
            for category in self._state.categories.values()
            if category.section_id == section_id
        ]


class FakeDocumentRepository:
    """Fake documents."""

    def __init__(
        self,
        state: FakeState,
    ) -> None:
        """Сохраняет state."""
        self._state = state

    async def get_for_update(
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

    async def update(
        self,
        document: NormativeDocument,
    ) -> None:
        """Обновляет document."""
        self._state.documents[document.document_id] = document


class FakeUnitOfWork:
    """Fake catalog Unit of Work."""

    def __init__(
        self,
        state: FakeState,
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


class FakeVectorStore:
    """Vector store, который не должен вызываться в mismatch test."""

    async def set_payload_by_filter(
        self,
        **kwargs: object,
    ) -> None:
        """Неожиданный вызов считается ошибкой."""
        raise AssertionError(
            f"Vector store вызван неожиданно: {kwargs}",
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


def make_section() -> NormativeSection:
    """Создаёт test section."""
    return NormativeSection(
        section_id=SECTION_ID,
        name="КИПиА",
        system_prompt="Test.",
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )


def make_category(
    *,
    category_id: UUID,
    area: CatalogArea = CatalogArea.NORMATIVE,
) -> NormativeCategory:
    """Создаёт category."""
    return NormativeCategory(
        category_id=category_id,
        section_id=SECTION_ID,
        parent_id=None,
        name=str(
            area.value,
        ),
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
        area=area,
    )


def make_document(
    *,
    document_id: UUID,
    area: CatalogArea = CatalogArea.NORMATIVE,
) -> NormativeDocument:
    """Создаёт uploaded document."""
    return NormativeDocument(
        document_id=document_id,
        section_id=SECTION_ID,
        category_id=None,
        original_name=f"{document_id}.pdf",
        storage_key=f"{SECTION_ID}/{document_id}.pdf",
        mime_type="application/pdf",
        size_bytes=10,
        sha256="a" * 64,
        index_status=IndexingStatus.UPLOADED,
        index_error=None,
        indexed_at=None,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
        area=area,
    )


@pytest.mark.asyncio
async def test_existing_entities_default_to_normative_area() -> None:
    """Legacy constructors сохраняют старое поведение."""
    category = make_category(
        category_id=NORMATIVE_CATEGORY_ID,
    )

    document = make_document(
        document_id=NORMATIVE_DOCUMENT_ID,
    )

    assert category.area is CatalogArea.NORMATIVE

    assert document.area is CatalogArea.NORMATIVE


@pytest.mark.asyncio
async def test_category_listing_is_scoped_by_area() -> None:
    """Normative и user-package folders возвращаются отдельно."""
    state = FakeState()

    state.sections[SECTION_ID] = make_section()

    state.categories[NORMATIVE_CATEGORY_ID] = make_category(
        category_id=NORMATIVE_CATEGORY_ID,
    )

    state.categories[PACKAGE_CATEGORY_ID] = make_category(
        category_id=PACKAGE_CATEGORY_ID,
        area=CatalogArea.USER_PACKAGE,
    )

    use_case = ListNormativeCategories(
        unit_of_work_factory=factory(
            state,
        ),
    )

    normative = await use_case.execute(
        section_id=SECTION_ID,
    )

    packages = await use_case.execute(
        section_id=SECTION_ID,
        area=CatalogArea.USER_PACKAGE,
    )

    assert [item.category_id for item in normative] == [
        NORMATIVE_CATEGORY_ID,
    ]

    assert [item.category_id for item in packages] == [
        PACKAGE_CATEGORY_ID,
    ]


@pytest.mark.asyncio
async def test_user_package_cannot_use_normative_parent() -> None:
    """Папки разных areas нельзя соединять hierarchy."""
    state = FakeState()

    state.sections[SECTION_ID] = make_section()

    state.categories[NORMATIVE_CATEGORY_ID] = make_category(
        category_id=NORMATIVE_CATEGORY_ID,
    )

    use_case = CreateNormativeCategory(
        unit_of_work_factory=factory(
            state,
        ),
        clock=lambda: BASE_TIME,
        identifier_factory=lambda: NEW_CATEGORY_ID,
    )

    with pytest.raises(
        NormativeCategoryParentError,
        match="другой области",
    ):
        await use_case.execute(
            section_id=SECTION_ID,
            name="Пакет",
            parent_id=NORMATIVE_CATEGORY_ID,
            area=CatalogArea.USER_PACKAGE,
        )

    assert NEW_CATEGORY_ID not in state.categories

    assert state.commits == 0


@pytest.mark.asyncio
async def test_document_listing_is_scoped_by_area() -> None:
    """Документы normative/user-package разделяются."""
    state = FakeState()

    state.sections[SECTION_ID] = make_section()

    state.documents[NORMATIVE_DOCUMENT_ID] = make_document(
        document_id=NORMATIVE_DOCUMENT_ID,
    )

    state.documents[PACKAGE_DOCUMENT_ID] = make_document(
        document_id=PACKAGE_DOCUMENT_ID,
        area=CatalogArea.USER_PACKAGE,
    )

    use_case = ListNormativeDocuments(
        unit_of_work_factory=factory(
            state,
        ),
    )

    normative = await use_case.execute(
        section_id=SECTION_ID,
    )

    packages = await use_case.execute(
        section_id=SECTION_ID,
        area=CatalogArea.USER_PACKAGE,
    )

    assert [item.document_id for item in normative] == [
        NORMATIVE_DOCUMENT_ID,
    ]

    assert [item.document_id for item in packages] == [
        PACKAGE_DOCUMENT_ID,
    ]


@pytest.mark.asyncio
async def test_document_cannot_move_to_category_from_other_area() -> None:
    """User-package document нельзя положить в normative folder."""
    state = FakeState()

    state.sections[SECTION_ID] = make_section()

    state.categories[NORMATIVE_CATEGORY_ID] = make_category(
        category_id=NORMATIVE_CATEGORY_ID,
    )

    state.documents[PACKAGE_DOCUMENT_ID] = make_document(
        document_id=PACKAGE_DOCUMENT_ID,
        area=CatalogArea.USER_PACKAGE,
    )

    use_case = MoveNormativeDocument(
        unit_of_work_factory=factory(
            state,
        ),
        vector_store=FakeVectorStore(),
        collection="test",
        clock=lambda: CHANGED_TIME,
    )

    with pytest.raises(
        NormativeDocumentCategoryError,
        match="другой области",
    ):
        await use_case.execute(
            document_id=PACKAGE_DOCUMENT_ID,
            category_id=NORMATIVE_CATEGORY_ID,
        )

    assert state.documents[PACKAGE_DOCUMENT_ID].category_id is None

    assert state.commits == 0
