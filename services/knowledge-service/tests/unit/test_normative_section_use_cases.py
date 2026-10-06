# services/knowledge-service/tests/unit/test_normative_section_use_cases.py

"""Проверки прикладных сценариев нормативных разделов."""

from dataclasses import (
    dataclass,
    field,
    replace,
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
    NormativeSectionNotFoundError,
    UpdateNormativeSection,
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
    2,
    12,
    0,
    tzinfo=UTC,
)

SECTION_ID = UUID("11111111-1111-1111-1111-111111111111")


@dataclass
class FakeCatalogState:
    """Общее состояние нескольких тестовых транзакций в памяти."""

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
        self._state.sections.pop(section_id, None)
        self._state.categories[:] = [
            c for c in self._state.categories if c.section_id != section_id
        ]
        self._state.documents[:] = [
            d for d in self._state.documents if d.section_id != section_id
        ]


class FakeCategoryRepository:
    """Минимальный репозиторий папок для проверки разделов."""

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
    """Минимальный репозиторий документов для проверки разделов."""

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

    async def get_for_update(self, document_id: UUID) -> NormativeDocument | None:
        """Возвращает документ текущего fake состояния."""
        return next(
            (d for d in self._state.documents if d.document_id == document_id), None
        )

    async def update(self, document: NormativeDocument) -> None:
        """Обновляет метаданные, сохраняя соседние документы."""
        self._state.documents[:] = [
            document if d.document_id == document.document_id else d
            for d in self._state.documents
        ]

    async def delete(self, document_id: UUID) -> None:
        """Идемпотентно удаляет документ по UUID."""
        self._state.documents[:] = [
            d for d in self._state.documents if d.document_id != document_id
        ]


class FakeUnitOfWork:
    """Имитирует транзакцию сценариев раздела в памяти."""

    def __init__(
        self,
        state: FakeCatalogState,
    ) -> None:
        """Создаёт репозитории поверх общего тестового состояния."""
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
        """Изолированным проверкам не требуется имитация отката."""
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
    """Создаёт фабрику новых тестовых транзакций."""
    return lambda: FakeUnitOfWork(
        state,
    )


@pytest.mark.asyncio
async def test_create_section_uses_default_prompt() -> None:
    """Новый раздел получает системный промпт по умолчанию."""
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
    """Сценарии чтения возвращают список разделов и отдельный раздел."""
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
    """Несуществующий UUID превращается в прикладную ошибку."""
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


class FakeStorage:
    """Записывает очистку конкретных файлов и папки раздела."""

    def __init__(self) -> None:
        """Готовит журнал и переключатель внешнего сбоя."""
        self.deleted = []
        self.sections = []
        self.fail = False

    async def delete(self, *, storage_key: str) -> None:
        """Повторяемо удаляет файл либо имитирует недоступность хранилища."""
        if self.fail:
            raise RuntimeError("storage offline")
        self.deleted.append(storage_key)

    async def delete_section(self, *, section_id: UUID) -> None:
        """Записывает удаление папки одного UUID."""
        self.sections.append(section_id)


class FakeVectors:
    """Удаляет исключительно фильтрованные вектора общей коллекции."""

    def __init__(self) -> None:
        """Создаёт журнал фильтров."""
        self.deleted = []

    async def delete_by_filter(self, **kwargs) -> None:
        """Записывает фильтр без операции над всей коллекцией."""
        self.deleted.append(kwargs)


@pytest.mark.asyncio
async def test_delete_non_empty_section_and_repeat() -> None:
    """Каскад удаляет папки обеих областей и не задевает соседний раздел."""
    state = FakeCatalogState()
    state.sections[SECTION_ID] = make_section()
    other = uuid4()
    state.sections[other] = replace(make_section(), section_id=other)
    state.categories.extend(
        [
            NormativeCategory(
                category_id=uuid4(),
                section_id=SECTION_ID,
                parent_id=None,
                name="СП",
                created_at=BASE_TIME,
                updated_at=BASE_TIME,
            ),
            NormativeCategory(
                category_id=uuid4(),
                section_id=other,
                parent_id=None,
                name="Другой",
                created_at=BASE_TIME,
                updated_at=BASE_TIME,
            ),
        ]
    )
    storage, vectors = FakeStorage(), FakeVectors()
    use_case = DeleteNormativeSection(build_factory(state), storage, vectors, "shared")
    assert await use_case.execute(section_id=SECTION_ID) == SECTION_ID
    assert await use_case.execute(section_id=SECTION_ID) == SECTION_ID
    assert set(state.sections) == {other}
    assert len(state.categories) == 1
    assert vectors.deleted == [
        {"collection": "shared", "key": "section_id", "value": str(SECTION_ID)}
    ]
    assert storage.sections == [SECTION_ID]
    assert state.commits == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", list(IndexingStatus))
async def test_partial_delete_retains_metadata_and_resumes(status) -> None:
    """После сбоя хранилища повтор очищает обе области из любого состояния индексации."""
    state = FakeCatalogState()
    state.sections[SECTION_ID] = make_section()
    for area in CatalogArea:
        doc_id = uuid4()
        state.documents.append(
            NormativeDocument(
                document_id=doc_id,
                section_id=SECTION_ID,
                category_id=None,
                original_name="file.pdf",
                storage_key=f"{SECTION_ID}/{doc_id}.pdf",
                mime_type="application/pdf",
                size_bytes=12,
                sha256="a" * 64,
                index_status=status,
                index_error="failed" if status is IndexingStatus.FAILED else None,
                indexed_at=BASE_TIME if status is IndexingStatus.READY else None,
                created_at=BASE_TIME,
                updated_at=BASE_TIME,
                area=area,
                owner_user_id=uuid4() if area is CatalogArea.USER_PACKAGE else None,
            )
        )
    storage, vectors = FakeStorage(), FakeVectors()
    use_case = DeleteNormativeSection(build_factory(state), storage, vectors, "shared")
    storage.fail = True
    with pytest.raises(RuntimeError, match="storage offline"):
        await use_case.execute(section_id=SECTION_ID)
    assert state.sections[SECTION_ID].deleting
    assert len(state.documents) == 2
    assert state.documents[0].index_status is IndexingStatus.DELETING
    storage.fail = False
    assert await use_case.execute(section_id=SECTION_ID) == SECTION_ID
    assert state.documents == []
    assert SECTION_ID not in state.sections
    assert len(storage.deleted) == 4
    assert {v["collection"] for v in vectors.deleted} == {"shared"}
    assert vectors.deleted[-1] == {
        "collection": "shared",
        "key": "section_id",
        "value": str(SECTION_ID),
    }
