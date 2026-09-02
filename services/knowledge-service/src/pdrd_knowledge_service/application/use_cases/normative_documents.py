# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/normative_documents.py

"""Read use cases документов управляемой нормативной базы."""

from dataclasses import dataclass
from uuid import UUID

from pdrd_knowledge_service.application.ports.persistence import (
    NormativeCatalogUnitOfWork,
    NormativeCatalogUnitOfWorkFactory,
)
from pdrd_knowledge_service.application.use_cases.normative_sections import (
    NormativeSectionNotFoundError,
)
from pdrd_knowledge_service.domain.normative_catalog import (
    NormativeDocument,
)


class NormativeDocumentNotFoundError(LookupError):
    """Запрошенный нормативный документ не найден."""


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


@dataclass(frozen=True, slots=True)
class ListNormativeDocuments:
    """Возвращает документы нормативного раздела."""

    unit_of_work_factory: NormativeCatalogUnitOfWorkFactory

    async def execute(
        self,
        *,
        section_id: UUID,
    ) -> tuple[
        NormativeDocument,
        ...,
    ]:
        """Возвращает все documents вместе с indexing status."""
        async with self.unit_of_work_factory() as unit_of_work:
            await _require_section(
                unit_of_work,
                section_id,
            )

            documents = await unit_of_work.documents.list_by_section(
                section_id,
            )

        return tuple(
            documents,
        )


@dataclass(frozen=True, slots=True)
class GetNormativeDocument:
    """Возвращает metadata одного нормативного документа."""

    unit_of_work_factory: NormativeCatalogUnitOfWorkFactory

    async def execute(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocument:
        """Загружает document по UUID."""
        async with self.unit_of_work_factory() as unit_of_work:
            document = await unit_of_work.documents.get(
                document_id,
            )

        if document is None:
            raise NormativeDocumentNotFoundError(
                f"Нормативный документ {document_id} не найден.",
            )

        return document


@dataclass(frozen=True, slots=True)
class NormativeDocumentQueryUseCases:
    """Группирует read operations нормативных документов."""

    list_documents: ListNormativeDocuments

    get_document: GetNormativeDocument
