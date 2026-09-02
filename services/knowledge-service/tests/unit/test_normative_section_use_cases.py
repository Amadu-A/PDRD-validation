# services/knowledge-service/tests/unit/test_normative_section_use_cases.py

"""Unit tests application use cases нормативных разделов."""

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
from uuid import (
    UUID,
    uuid4,
)

import pytest
from pdrd_knowledge_service.application.normative_catalog_defaults import (
    DEFAULT_SECTION_SYSTEM_PROMPT,
)
from pdrd_knowledge_service.application.use_cases.normative_sections import (
    CreateNormativeSection,
    DeleteNormativeSection,
    GetNormativeSection,
    ListNormativeSections,
    NormativeSectionNotEmptyError,
    NormativeSectionNotFoundError,
    UpdateNormativeSection,
)
from pdrd_knowledge_service.domain.normative_catalog import (
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

SECTION_ID = UUID("11111111-1111-1111-1111-111111111111")


@dataclass
class FakeCatalogState:
    """Общее in-memory состояние нескольких Unit of Work."""

    sections: dict[
        UUID,
        NormativeSection,
    ] = field(
        default_factory=dict,
    )

    categories: list[NormativeCategory] = field(
        default_factory=list,
    )

    documents: list[NormativeDocument] = field(
        default_factory=list,
    )

    commits: int = 0


class FakeSectionRepository:
    """In-memory repository разделов."""

    def __init__(
        self,
        state: FakeCatalogState,
    ) -> None:
        """Сохраняет test state."""
        self._state = state

    async def add(
        self,
        section: NormativeSection,
    ) -> None:
        """Добавляет раздел."""
        self._state.sections[section.section_id] = section

    async def get(
        self,
        section_id: UUID,
    ) -> NormativeSection | None:
        """Возвращает раздел."""
        return self._state.sections.get(
            section_id,
        )

    async def list_all(
        self,
    ) -> list[NormativeSection]:
        """Возвращает разделы."""
        return list(
            self._state.sections.values(),
        )

    async def update(
        self,
        section: NormativeSection,
    ) -> None:
        """Обновляет раздел."""
        self._state.sections[section.section_id] = section

    async def delete(
        self,
        section_id: UUID,
    ) -> None:
        """Удаляет раздел."""
        self._state.sections.pop(
            section_id,
            None,
        )


class FakeCategoryRepository:
    """Минимальный repository категорий для section tests."""

    def __init__(
        self,
        state: FakeCatalogState,
    ) -> None:
        """Сохраняет test state."""
        self._state = state

    async def list_by_section(
        self,
        section_id: UUID,
    ) -> list[NormativeCategory]:
        """Возвращает категории раздела."""
        return [
            category
            for category in self._state.categories
            if category.section_id == section_id
        ]


class FakeDocumentRepository:
    """Минимальный repository документов для section tests."""

    def __init__(
        self,
        state: FakeCatalogState,
    ) -> None:
        """Сохраняет test state."""
        self._state = state

    async def list_by_section(
        self,
        section_id: UUID,
    ) -> list[NormativeDocument]:
        """Возвращает документы раздела."""
        return [
            document
            for document in self._state.documents
            if document.section_id == section_id
        ]


class FakeUnitOfWork:
    """In-memory Unit of Work section use cases."""

    def __init__(
        self,
        state: FakeCatalogState,
    ) -> None:
        """Создаёт repositories поверх общего test state."""
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
        """Fake rollback не нужен этим unit tests."""
        return None


def make_section() -> NormativeSection:
    """Создаёт существующий test section."""
    return NormativeSection(
        section_id=SECTION_ID,
        name="ЭОМ",
        system_prompt="Сохранённый prompt.",
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )


def build_factory(
    state: FakeCatalogState,
):
    """Создаёт factory новых fake Unit of Work."""
    return lambda: FakeUnitOfWork(
        state,
    )


@pytest.mark.asyncio
async def test_create_section_uses_default_prompt() -> None:
    """Новый раздел автоматически получает default system prompt."""
    state = FakeCatalogState()

    use_case = CreateNormativeSection(
        unit_of_work_factory=build_factory(
            state,
        ),
        default_system_prompt=(DEFAULT_SECTION_SYSTEM_PROMPT),
        clock=lambda: BASE_TIME,
        identifier_factory=lambda: SECTION_ID,
    )

    created = await use_case.execute(
        name="  Электроснабжение  ",
    )

    assert created.section_id == SECTION_ID
    assert created.name == "Электроснабжение"

    assert created.system_prompt == (DEFAULT_SECTION_SYSTEM_PROMPT)

    assert state.sections[SECTION_ID] == created

    assert state.commits == 1


@pytest.mark.asyncio
async def test_list_and_get_sections() -> None:
    """Разделы доступны через list и get use cases."""
    state = FakeCatalogState()

    section = make_section()

    state.sections[SECTION_ID] = section

    factory = build_factory(
        state,
    )

    listed = await ListNormativeSections(
        unit_of_work_factory=factory,
    ).execute()

    loaded = await GetNormativeSection(
        unit_of_work_factory=factory,
    ).execute(
        section_id=SECTION_ID,
    )

    assert listed == (section,)

    assert loaded == section


@pytest.mark.asyncio
async def test_get_missing_section_fails() -> None:
    """Несуществующий UUID превращается в application error."""
    state = FakeCatalogState()

    use_case = GetNormativeSection(
        unit_of_work_factory=build_factory(
            state,
        ),
    )

    with pytest.raises(
        NormativeSectionNotFoundError,
    ):
        await use_case.execute(
            section_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_update_preserves_exact_prompt_text() -> None:
    """Prompt сохраняется без strip или иной нормализации."""
    state = FakeCatalogState()

    state.sections[SECTION_ID] = make_section()

    changed_at = BASE_TIME + timedelta(
        minutes=1,
    )

    use_case = UpdateNormativeSection(
        unit_of_work_factory=build_factory(
            state,
        ),
        clock=lambda: changed_at,
    )

    prompt = "  строка 1\nстрока 2  "

    updated = await use_case.execute(
        section_id=SECTION_ID,
        name="  Новый ЭОМ  ",
        system_prompt=prompt,
    )

    assert updated.name == "Новый ЭОМ"
    assert updated.system_prompt == prompt
    assert updated.updated_at == changed_at
    assert state.commits == 1


@pytest.mark.asyncio
async def test_delete_non_empty_section_is_rejected() -> None:
    """Раздел с category нельзя удалить обычным SQL cascade."""
    state = FakeCatalogState()

    state.sections[SECTION_ID] = make_section()

    state.categories.append(
        NormativeCategory(
            category_id=uuid4(),
            section_id=SECTION_ID,
            parent_id=None,
            name="СП",
            created_at=BASE_TIME,
            updated_at=BASE_TIME,
        )
    )

    use_case = DeleteNormativeSection(
        unit_of_work_factory=build_factory(
            state,
        ),
    )

    with pytest.raises(
        NormativeSectionNotEmptyError,
    ):
        await use_case.execute(
            section_id=SECTION_ID,
        )

    assert SECTION_ID in state.sections
    assert state.commits == 0


@pytest.mark.asyncio
async def test_delete_empty_section() -> None:
    """Пустой раздел удаляется одной transaction."""
    state = FakeCatalogState()

    state.sections[SECTION_ID] = make_section()

    use_case = DeleteNormativeSection(
        unit_of_work_factory=build_factory(
            state,
        ),
    )

    deleted_id = await use_case.execute(
        section_id=SECTION_ID,
    )

    assert deleted_id == SECTION_ID
    assert SECTION_ID not in state.sections
    assert state.commits == 1
