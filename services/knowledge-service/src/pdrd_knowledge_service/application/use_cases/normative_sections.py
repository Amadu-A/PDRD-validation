# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/normative_sections.py

"""Use cases разделов управляемой нормативной базы."""

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
from pdrd_knowledge_service.domain.normative_catalog import (
    NormativeSection,
)

Clock = Callable[
    [],
    datetime,
]

IdentifierFactory = Callable[
    [],
    UUID,
]


class NormativeSectionNotFoundError(LookupError):
    """Запрошенный раздел нормативной базы не найден."""


class NormativeSectionNotEmptyError(RuntimeError):
    """Нельзя удалить непустой раздел нормативной базы."""


class NormativeSectionUpdateError(ValueError):
    """Некорректный запрос изменения раздела."""


def utc_now() -> datetime:
    """Возвращает текущее timezone-aware UTC время."""
    return datetime.now(
        UTC,
    )


async def _require_section(
    unit_of_work: NormativeCatalogUnitOfWork,
    section_id: UUID,
) -> NormativeSection:
    """Возвращает раздел или формирует application error."""
    section = await unit_of_work.sections.get(
        section_id,
    )

    if section is None:
        raise NormativeSectionNotFoundError(
            f"Раздел нормативной базы {section_id} не найден.",
        )

    return section


@dataclass(frozen=True, slots=True)
class ListNormativeSections:
    """Возвращает доступные разделы нормативной базы."""

    unit_of_work_factory: NormativeCatalogUnitOfWorkFactory

    async def execute(
        self,
    ) -> tuple[
        NormativeSection,
        ...,
    ]:
        """Возвращает стабильный список разделов."""
        async with self.unit_of_work_factory() as unit_of_work:
            sections = await unit_of_work.sections.list_all()

        return tuple(
            sections,
        )


@dataclass(frozen=True, slots=True)
class GetNormativeSection:
    """Возвращает один раздел нормативной базы."""

    unit_of_work_factory: NormativeCatalogUnitOfWorkFactory

    async def execute(
        self,
        *,
        section_id: UUID,
    ) -> NormativeSection:
        """Загружает раздел по UUID."""
        async with self.unit_of_work_factory() as unit_of_work:
            return await _require_section(
                unit_of_work,
                section_id,
            )


@dataclass(frozen=True, slots=True)
class CreateNormativeSection:
    """Создаёт раздел с default system prompt."""

    unit_of_work_factory: NormativeCatalogUnitOfWorkFactory

    default_system_prompt: str

    clock: Clock = utc_now

    identifier_factory: IdentifierFactory = uuid4

    async def execute(
        self,
        *,
        name: str,
    ) -> NormativeSection:
        """Создаёт и атомарно сохраняет новый раздел."""
        created_at = self.clock()

        section = NormativeSection(
            section_id=self.identifier_factory(),
            name=name.strip(),
            system_prompt=self.default_system_prompt,
            created_at=created_at,
            updated_at=created_at,
        )

        async with self.unit_of_work_factory() as unit_of_work:
            await unit_of_work.sections.add(
                section,
            )

            await unit_of_work.commit()

        return section


@dataclass(frozen=True, slots=True)
class UpdateNormativeSection:
    """Изменяет имя и сохранённый system prompt раздела."""

    unit_of_work_factory: NormativeCatalogUnitOfWorkFactory

    clock: Clock = utc_now

    async def execute(
        self,
        *,
        section_id: UUID,
        name: str | None = None,
        system_prompt: str | None = None,
    ) -> NormativeSection:
        """Атомарно обновляет переданные поля раздела."""
        if name is None and system_prompt is None:
            raise NormativeSectionUpdateError(
                "Не передано ни одного поля для изменения раздела.",
            )

        async with self.unit_of_work_factory() as unit_of_work:
            section = await _require_section(
                unit_of_work,
                section_id,
            )

            changed_at = self.clock()

            if name is not None:
                section = section.renamed(
                    name=name.strip(),
                    changed_at=changed_at,
                )

            if system_prompt is not None:
                section = section.with_system_prompt(
                    system_prompt=system_prompt,
                    changed_at=changed_at,
                )

            await unit_of_work.sections.update(
                section,
            )

            await unit_of_work.commit()

        return section


@dataclass(frozen=True, slots=True)
class DeleteNormativeSection:
    """Удаляет только пустой раздел нормативной базы."""

    unit_of_work_factory: NormativeCatalogUnitOfWorkFactory

    async def execute(
        self,
        *,
        section_id: UUID,
    ) -> UUID:
        """Удаляет раздел, если в нём нет категорий и документов."""
        async with self.unit_of_work_factory() as unit_of_work:
            await _require_section(
                unit_of_work,
                section_id,
            )

            categories = await unit_of_work.categories.list_by_section(
                section_id,
            )

            documents = await unit_of_work.documents.list_by_section(
                section_id,
            )

            if categories or documents:
                raise NormativeSectionNotEmptyError(
                    "Нельзя удалить непустой раздел нормативной базы.",
                )

            await unit_of_work.sections.delete(
                section_id,
            )

            await unit_of_work.commit()

        return section_id


@dataclass(frozen=True, slots=True)
class NormativeSectionUseCases:
    """Группирует application operations одного bounded context."""

    list_sections: ListNormativeSections

    get_section: GetNormativeSection

    create_section: CreateNormativeSection

    update_section: UpdateNormativeSection

    delete_section: DeleteNormativeSection
