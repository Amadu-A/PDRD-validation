# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/normative_categories.py

"""Use cases категорий управляемой нормативной базы."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import (
    UTC,
    datetime,
)
from uuid import (
    UUID,
    uuid4,
)

from pdrd_knowledge_service.application.ports.persistence import (
    NormativeCatalogUnitOfWork,
    NormativeCatalogUnitOfWorkFactory,
)
from pdrd_knowledge_service.application.use_cases.normative_sections import (
    NormativeSectionNotFoundError,
)
from pdrd_knowledge_service.domain.normative_catalog import (
    NormativeCategory,
)

Clock = Callable[
    [],
    datetime,
]

IdentifierFactory = Callable[
    [],
    UUID,
]


class NormativeCategoryNotFoundError(LookupError):
    """Запрошенная категория нормативной базы не найдена."""


class NormativeCategoryParentError(ValueError):
    """Некорректная parent category или hierarchy."""


class NormativeCategoryUpdateError(ValueError):
    """Некорректный запрос изменения категории."""


def utc_now() -> datetime:
    """Возвращает текущее timezone-aware UTC время."""
    return datetime.now(
        UTC,
    )


async def _require_section(
    unit_of_work: NormativeCatalogUnitOfWork,
    section_id: UUID,
) -> None:
    """Проверяет существование нормативного раздела."""
    section = await unit_of_work.sections.get(
        section_id,
    )

    if section is None:
        raise NormativeSectionNotFoundError(
            f"Раздел нормативной базы {section_id} не найден.",
        )


async def _require_category(
    unit_of_work: NormativeCatalogUnitOfWork,
    category_id: UUID,
) -> NormativeCategory:
    """Возвращает category или формирует application error."""
    category = await unit_of_work.categories.get(
        category_id,
    )

    if category is None:
        raise NormativeCategoryNotFoundError(
            f"Категория нормативной базы {category_id} не найдена.",
        )

    return category


async def _validate_parent(
    unit_of_work: NormativeCatalogUnitOfWork,
    *,
    section_id: UUID,
    category_id: UUID,
    parent_id: UUID | None,
) -> None:
    """Проверяет section принадлежность и отсутствие hierarchy cycle."""
    if parent_id is None:
        return

    if parent_id == category_id:
        raise NormativeCategoryParentError(
            "Категория не может быть родителем самой себя.",
        )

    parent = await unit_of_work.categories.get(
        parent_id,
    )

    if parent is None:
        raise NormativeCategoryParentError(
            f"Родительская категория {parent_id} не найдена.",
        )

    if parent.section_id != section_id:
        raise NormativeCategoryParentError(
            "Родительская категория принадлежит другому разделу.",
        )

    categories = await unit_of_work.categories.list_by_section(
        section_id,
    )

    categories_by_id = {category.category_id: category for category in categories}

    cursor_id: UUID | None = parent_id
    visited: set[UUID] = set()

    while cursor_id is not None:
        if cursor_id == category_id:
            raise NormativeCategoryParentError(
                "Перемещение создаёт цикл в дереве категорий.",
            )

        if cursor_id in visited:
            raise NormativeCategoryParentError(
                "В дереве категорий уже обнаружен цикл.",
            )

        visited.add(
            cursor_id,
        )

        cursor = categories_by_id.get(
            cursor_id,
        )

        if cursor is None:
            raise NormativeCategoryParentError(
                "Цепочка родительских категорий выходит за пределы текущего раздела.",
            )

        cursor_id = cursor.parent_id


@dataclass(frozen=True, slots=True)
class ListNormativeCategories:
    """Возвращает категории одного нормативного раздела."""

    unit_of_work_factory: NormativeCatalogUnitOfWorkFactory

    async def execute(
        self,
        *,
        section_id: UUID,
    ) -> tuple[
        NormativeCategory,
        ...,
    ]:
        """Возвращает категории раздела."""
        async with self.unit_of_work_factory() as unit_of_work:
            await _require_section(
                unit_of_work,
                section_id,
            )

            categories = await unit_of_work.categories.list_by_section(
                section_id,
            )

        return tuple(
            categories,
        )


@dataclass(frozen=True, slots=True)
class GetNormativeCategory:
    """Возвращает одну категорию нормативной базы."""

    unit_of_work_factory: NormativeCatalogUnitOfWorkFactory

    async def execute(
        self,
        *,
        category_id: UUID,
    ) -> NormativeCategory:
        """Загружает category по UUID."""
        async with self.unit_of_work_factory() as unit_of_work:
            return await _require_category(
                unit_of_work,
                category_id,
            )


@dataclass(frozen=True, slots=True)
class CreateNormativeCategory:
    """Создаёт категорию внутри нормативного раздела."""

    unit_of_work_factory: NormativeCatalogUnitOfWorkFactory

    clock: Clock = utc_now

    identifier_factory: IdentifierFactory = uuid4

    async def execute(
        self,
        *,
        section_id: UUID,
        name: str,
        parent_id: UUID | None,
    ) -> NormativeCategory:
        """Создаёт category и проверяет её parent."""
        created_at = self.clock()
        category_id = self.identifier_factory()

        async with self.unit_of_work_factory() as unit_of_work:
            await _require_section(
                unit_of_work,
                section_id,
            )

            await _validate_parent(
                unit_of_work,
                section_id=section_id,
                category_id=category_id,
                parent_id=parent_id,
            )

            category = NormativeCategory(
                category_id=category_id,
                section_id=section_id,
                parent_id=parent_id,
                name=name.strip(),
                created_at=created_at,
                updated_at=created_at,
            )

            await unit_of_work.categories.add(
                category,
            )

            await unit_of_work.commit()

        return category


@dataclass(frozen=True, slots=True)
class UpdateNormativeCategory:
    """Переименовывает или перемещает категорию."""

    unit_of_work_factory: NormativeCatalogUnitOfWorkFactory

    clock: Clock = utc_now

    async def execute(
        self,
        *,
        category_id: UUID,
        name: str | None = None,
        parent_id: UUID | None = None,
        change_parent: bool = False,
    ) -> NormativeCategory:
        """Обновляет только явно переданные свойства category."""
        if name is None and not change_parent:
            raise NormativeCategoryUpdateError(
                "Не передано ни одного поля для изменения категории.",
            )

        async with self.unit_of_work_factory() as unit_of_work:
            category = await _require_category(
                unit_of_work,
                category_id,
            )

            changed_at = self.clock()

            if change_parent:
                await _validate_parent(
                    unit_of_work,
                    section_id=category.section_id,
                    category_id=category.category_id,
                    parent_id=parent_id,
                )

            if name is not None:
                category = category.renamed(
                    name=name.strip(),
                    changed_at=changed_at,
                )

            if change_parent:
                category = category.moved_under(
                    parent_id=parent_id,
                    changed_at=changed_at,
                )

            await unit_of_work.categories.update(
                category,
            )

            await unit_of_work.commit()

        return category


@dataclass(frozen=True, slots=True)
class DeleteNormativeCategory:
    """Удаляет category без удаления нормативных документов."""

    unit_of_work_factory: NormativeCatalogUnitOfWorkFactory

    async def execute(
        self,
        *,
        category_id: UUID,
    ) -> UUID:
        """Удаляет category одной transaction."""
        async with self.unit_of_work_factory() as unit_of_work:
            await _require_category(
                unit_of_work,
                category_id,
            )

            await unit_of_work.categories.delete(
                category_id,
            )

            await unit_of_work.commit()

        return category_id


@dataclass(frozen=True, slots=True)
class NormativeCategoryUseCases:
    """Группирует application operations категорий."""

    list_categories: ListNormativeCategories

    get_category: GetNormativeCategory

    create_category: CreateNormativeCategory

    update_category: UpdateNormativeCategory

    delete_category: DeleteNormativeCategory
