# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/normative_sections.py

"""Use cases разделов управляемой нормативной базы."""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import (
    UTC,
    datetime,
)
from pathlib import PurePosixPath
from uuid import (
    UUID,
    uuid4,
)

from pdrd_knowledge_service.application.normative_document_formats import (
    preview_storage_key,
)
from pdrd_knowledge_service.application.ports.document_storage import (
    NormativeDocumentStorage,
    NormativeDocumentStorageError,
)
from pdrd_knowledge_service.application.ports.persistence import (
    NormativeCatalogUnitOfWork,
    NormativeCatalogUnitOfWorkFactory,
)
from pdrd_knowledge_service.application.ports.section_locks import (
    CatalogSectionLocks,
    InMemoryCatalogSectionLocks,
)
from pdrd_knowledge_service.application.ports.vector_store import VectorStore
from pdrd_knowledge_service.core.observability import log_execution_time
from pdrd_knowledge_service.domain.normative_catalog import (
    IndexingStatus,
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
    """Возвращает текущее время UTC с часовым поясом."""
    return datetime.now(
        UTC,
    )


async def _require_section(
    unit_of_work: NormativeCatalogUnitOfWork,
    section_id: UUID,
    *,
    allow_deleting: bool = False,
) -> NormativeSection:
    """Возвращает раздел или формирует application error."""
    section = await unit_of_work.sections.get(
        section_id,
    )

    if section is None or (section.deleting and not allow_deleting):
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
                allow_deleting=True,
            )


@dataclass(frozen=True, slots=True)
class CreateNormativeSection:
    """Создаёт раздел с системным промптом по умолчанию."""

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

    section_locks: CatalogSectionLocks = field(
        default_factory=InMemoryCatalogSectionLocks
    )

    clock: Clock = utc_now

    async def execute(
        self,
        *,
        section_id: UUID,
        name: str | None = None,
        system_prompt: str | None = None,
    ) -> NormativeSection:
        """Согласует обновление с каскадным удалением раздела."""
        async with self.section_locks.shared(section_id):
            return await self._execute(
                section_id=section_id, name=name, system_prompt=system_prompt
            )

    async def _execute(
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
    """Повторяемо удаляет раздел и обе области каталога из всех хранилищ."""

    unit_of_work_factory: NormativeCatalogUnitOfWorkFactory
    storage: NormativeDocumentStorage
    vector_store: VectorStore
    collection: str
    section_locks: CatalogSectionLocks = field(
        default_factory=InMemoryCatalogSectionLocks
    )
    clock: Clock = utc_now

    @log_execution_time(operation="normative_section_delete")
    async def execute(self, *, section_id: UUID) -> UUID:
        """Ждёт индексаторы, сохраняет отметку удаления и очищает только этот UUID."""
        async with self.section_locks.exclusive(section_id):
            async with self.unit_of_work_factory() as unit_of_work:
                section = await unit_of_work.sections.get(section_id)
                if section is None:
                    return section_id
                if not section.deleting:
                    await unit_of_work.sections.update(replace(section, deleting=True))
                    await unit_of_work.commit()
                documents = await unit_of_work.documents.list_by_section(section_id)

            # SQL сохраняет storage_key до завершения внешней очистки, поэтому retry
            # повторяет идемпотентные операции и не теряет оставшиеся оригиналы.
            for candidate in documents:
                async with self.unit_of_work_factory() as unit_of_work:
                    document = await unit_of_work.documents.get_for_update(
                        candidate.document_id
                    )
                    if document is None:
                        continue
                    if document.index_status is not IndexingStatus.DELETING:
                        document = document.transition_indexing(
                            target_status=IndexingStatus.DELETING,
                            changed_at=self.clock(),
                        )
                        await unit_of_work.documents.update(document)
                        await unit_of_work.commit()
                if PurePosixPath(document.storage_key).parts[0] != str(section_id):
                    raise NormativeDocumentStorageError(
                        "Файл документа находится вне папки удаляемого раздела."
                    )
                await self.vector_store.delete_by_filter(
                    collection=self.collection,
                    key="document_id",
                    value=str(document.document_id),
                )
                await self.storage.delete(storage_key=document.storage_key)
                # Удаление отсутствующего preview безопасно и очищает старые артефакты
                # после конверсии независимо от текущего MIME документа.
                await self.storage.delete(
                    storage_key=preview_storage_key(document.storage_key)
                )
                async with self.unit_of_work_factory() as unit_of_work:
                    await unit_of_work.documents.delete(document.document_id)
                    await unit_of_work.commit()

            # Убираем также осиротевшие points раздела; общую collection не удаляем.
            await self.vector_store.delete_by_filter(
                collection=self.collection, key="section_id", value=str(section_id)
            )
            await self.storage.delete_section(section_id=section_id)
            async with self.unit_of_work_factory() as unit_of_work:
                await unit_of_work.sections.delete(section_id)
                await unit_of_work.commit()
        return section_id


@dataclass(frozen=True, slots=True)
class NormativeSectionUseCases:
    """Группирует прикладные операции управления разделами."""

    list_sections: ListNormativeSections

    get_section: GetNormativeSection

    create_section: CreateNormativeSection

    update_section: UpdateNormativeSection

    delete_section: DeleteNormativeSection
