# services/knowledge-service/tests/unit/test_normative_category_use_cases.py

"""Unit tests application use cases нормативных категорий."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import (
    UTC,
    datetime,
    timedelta,
)
from types import TracebackType
from uuid import (
    UUID,
)

import pytest
from pdrd_knowledge_service.application.use_cases.normative_categories import (
    CreateNormativeCategory,
    DeleteNormativeCategory,
    ListNormativeCategories,
    NormativeCategoryParentError,
    UpdateNormativeCategory,
)
from pdrd_knowledge_service.application.use_cases.normative_sections import (
    NormativeSectionNotFoundError,
)
from pdrd_knowledge_service.domain.normative_catalog import (
    NormativeCategory,
    NormativeSection,
)
from pdrd_knowledge_service.transport.http.schemas.normative_categories import (
    UpdateNormativeCategoryRequest,
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

SECTION_ID = UUID(
    "11111111-1111-1111-1111-111111111111",
)

OTHER_SECTION_ID = UUID(
    "22222222-2222-2222-2222-222222222222",
)

PARENT_ID = UUID(
    "33333333-3333-3333-3333-333333333333",
)

CHILD_ID = UUID(
    "44444444-4444-4444-4444-444444444444",
)


@dataclass
class FakeCatalogState:
    """Общее in-memory состояние fake Unit of Work."""

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

    commits: int = 0


class FakeSectionRepository:
    """Минимальный repository разделов."""

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


class FakeCategoryRepository:
    """In-memory repository категорий."""

    def __init__(
        self,
        state: FakeCatalogState,
    ) -> None:
        """Сохраняет test state."""
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
        """Возвращает категории section."""
        return [
            category
            for category in self._state.categories.values()
            if category.section_id == section_id
        ]

    async def update(
        self,
        category: NormativeCategory,
    ) -> None:
        """Обновляет category."""
        self._state.categories[category.category_id] = category

    async def delete(
        self,
        category_id: UUID,
    ) -> None:
        """Удаляет category."""
        self._state.categories.pop(
            category_id,
            None,
        )


class FakeDocumentRepository:
    """Placeholder repository документов."""

    pass


class FakeUnitOfWork:
    """Fake Unit of Work category tests."""

    def __init__(
        self,
        state: FakeCatalogState,
    ) -> None:
        """Создаёт repositories поверх общего state."""
        self._state = state

        self.sections = FakeSectionRepository(
            state,
        )

        self.categories = FakeCategoryRepository(
            state,
        )

        self.documents = FakeDocumentRepository()

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


def build_factory(
    state: FakeCatalogState,
) -> Callable[
    [],
    FakeUnitOfWork,
]:
    """Создаёт Unit of Work factory."""
    return lambda: FakeUnitOfWork(
        state,
    )


def make_section(
    *,
    section_id: UUID = SECTION_ID,
    name: str = "ЭОМ",
) -> NormativeSection:
    """Создаёт test section."""
    return NormativeSection(
        section_id=section_id,
        name=name,
        system_prompt="Test prompt.",
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )


def make_category(
    *,
    category_id: UUID,
    section_id: UUID = SECTION_ID,
    parent_id: UUID | None = None,
    name: str,
) -> NormativeCategory:
    """Создаёт test category."""
    return NormativeCategory(
        category_id=category_id,
        section_id=section_id,
        parent_id=parent_id,
        name=name,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )


@pytest.mark.asyncio
async def test_create_root_category() -> None:
    """Новая category может находиться в корне section."""
    state = FakeCatalogState()

    state.sections[SECTION_ID] = make_section()

    use_case = CreateNormativeCategory(
        unit_of_work_factory=build_factory(
            state,
        ),
        clock=lambda: BASE_TIME,
        identifier_factory=lambda: PARENT_ID,
    )

    created = await use_case.execute(
        section_id=SECTION_ID,
        name="  СП  ",
        parent_id=None,
    )

    assert created.category_id == PARENT_ID
    assert created.name == "СП"
    assert created.parent_id is None
    assert state.commits == 1


@pytest.mark.asyncio
async def test_create_rejects_parent_from_another_section() -> None:
    """Parent category не может принадлежать другому section."""
    state = FakeCatalogState()

    state.sections[SECTION_ID] = make_section()

    state.sections[OTHER_SECTION_ID] = make_section(
        section_id=OTHER_SECTION_ID,
        name="СКС",
    )

    state.categories[PARENT_ID] = make_category(
        category_id=PARENT_ID,
        section_id=OTHER_SECTION_ID,
        name="Чужой parent",
    )

    use_case = CreateNormativeCategory(
        unit_of_work_factory=build_factory(
            state,
        ),
        clock=lambda: BASE_TIME,
        identifier_factory=lambda: CHILD_ID,
    )

    with pytest.raises(
        NormativeCategoryParentError,
    ):
        await use_case.execute(
            section_id=SECTION_ID,
            name="Дочерняя",
            parent_id=PARENT_ID,
        )

    assert CHILD_ID not in state.categories
    assert state.commits == 0


@pytest.mark.asyncio
async def test_update_renames_and_moves_category_to_root() -> None:
    """Category можно переименовать и перенести в root."""
    state = FakeCatalogState()

    state.sections[SECTION_ID] = make_section()

    state.categories[PARENT_ID] = make_category(
        category_id=PARENT_ID,
        name="Родитель",
    )

    state.categories[CHILD_ID] = make_category(
        category_id=CHILD_ID,
        parent_id=PARENT_ID,
        name="Старая",
    )

    use_case = UpdateNormativeCategory(
        unit_of_work_factory=build_factory(
            state,
        ),
        clock=lambda: CHANGED_TIME,
    )

    updated = await use_case.execute(
        category_id=CHILD_ID,
        name="  Новая  ",
        parent_id=None,
        change_parent=True,
    )

    assert updated.name == "Новая"
    assert updated.parent_id is None
    assert updated.updated_at == CHANGED_TIME
    assert state.commits == 1


@pytest.mark.asyncio
async def test_update_rejects_hierarchy_cycle() -> None:
    """Нельзя перенести parent внутрь собственного descendant."""
    state = FakeCatalogState()

    state.sections[SECTION_ID] = make_section()

    state.categories[PARENT_ID] = make_category(
        category_id=PARENT_ID,
        name="Родитель",
    )

    state.categories[CHILD_ID] = make_category(
        category_id=CHILD_ID,
        parent_id=PARENT_ID,
        name="Потомок",
    )

    use_case = UpdateNormativeCategory(
        unit_of_work_factory=build_factory(
            state,
        ),
        clock=lambda: CHANGED_TIME,
    )

    with pytest.raises(
        NormativeCategoryParentError,
        match="цикл",
    ):
        await use_case.execute(
            category_id=PARENT_ID,
            parent_id=CHILD_ID,
            change_parent=True,
        )

    assert state.categories[PARENT_ID].parent_id is None

    assert state.commits == 0


@pytest.mark.asyncio
async def test_delete_category() -> None:
    """Category удаляется одной transaction."""
    state = FakeCatalogState()

    state.sections[SECTION_ID] = make_section()

    state.categories[PARENT_ID] = make_category(
        category_id=PARENT_ID,
        name="Удаляемая",
    )

    use_case = DeleteNormativeCategory(
        unit_of_work_factory=build_factory(
            state,
        ),
    )

    deleted_id = await use_case.execute(
        category_id=PARENT_ID,
    )

    assert deleted_id == PARENT_ID
    assert PARENT_ID not in state.categories
    assert state.commits == 1


@pytest.mark.asyncio
async def test_list_requires_existing_section() -> None:
    """Нельзя запросить categories несуществующего section."""
    state = FakeCatalogState()

    use_case = ListNormativeCategories(
        unit_of_work_factory=build_factory(
            state,
        ),
    )

    with pytest.raises(
        NormativeSectionNotFoundError,
    ):
        await use_case.execute(
            section_id=SECTION_ID,
        )


def test_update_schema_distinguishes_null_parent_from_omitted_parent() -> None:
    """Explicit null означает перенос category в root."""
    move_to_root = UpdateNormativeCategoryRequest(
        parent_id=None,
    )

    rename_only = UpdateNormativeCategoryRequest(
        name="СП",
    )

    assert move_to_root.changes_parent is True
    assert rename_only.changes_parent is False
