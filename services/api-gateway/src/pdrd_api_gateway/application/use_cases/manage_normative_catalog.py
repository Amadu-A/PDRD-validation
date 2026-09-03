# services/api-gateway/src/pdrd_api_gateway/application/use_cases/manage_normative_catalog.py

"""Application facade managed normative catalog."""

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from pdrd_api_gateway.application.ports.normative_catalog_management import (
    NormativeCatalogManager,
    NormativeCategoryView,
    NormativeDocumentContent,
    NormativeDocumentView,
    NormativeSectionView,
)


@dataclass(frozen=True, slots=True)
class NormativeCatalogFacade:
    """Предоставляет Transport слою use cases нормативного каталога."""

    manager: NormativeCatalogManager

    async def list_sections(
        self,
    ) -> tuple[
        NormativeSectionView,
        ...,
    ]:
        """Возвращает разделы."""
        return await self.manager.list_sections()

    async def create_section(
        self,
        *,
        name: str,
    ) -> NormativeSectionView:
        """Создаёт раздел."""
        return await self.manager.create_section(
            name=name,
        )

    async def get_section(
        self,
        *,
        section_id: UUID,
    ) -> NormativeSectionView:
        """Возвращает раздел."""
        return await self.manager.get_section(
            section_id=section_id,
        )

    async def update_section(
        self,
        *,
        section_id: UUID,
        changes: Mapping[
            str,
            object,
        ],
    ) -> NormativeSectionView:
        """Изменяет раздел."""
        return await self.manager.update_section(
            section_id=section_id,
            changes=changes,
        )

    async def delete_section(
        self,
        *,
        section_id: UUID,
    ) -> UUID:
        """Удаляет пустой раздел."""
        return await self.manager.delete_section(
            section_id=section_id,
        )

    async def list_categories(
        self,
        *,
        section_id: UUID,
    ) -> tuple[
        NormativeCategoryView,
        ...,
    ]:
        """Возвращает категории раздела."""
        return await self.manager.list_categories(
            section_id=section_id,
        )

    async def create_category(
        self,
        *,
        section_id: UUID,
        name: str,
        parent_id: UUID | None,
    ) -> NormativeCategoryView:
        """Создаёт категорию."""
        return await self.manager.create_category(
            section_id=section_id,
            name=name,
            parent_id=parent_id,
        )

    async def get_category(
        self,
        *,
        category_id: UUID,
    ) -> NormativeCategoryView:
        """Возвращает категорию."""
        return await self.manager.get_category(
            category_id=category_id,
        )

    async def update_category(
        self,
        *,
        category_id: UUID,
        changes: Mapping[
            str,
            object,
        ],
    ) -> NormativeCategoryView:
        """Изменяет категорию."""
        return await self.manager.update_category(
            category_id=category_id,
            changes=changes,
        )

    async def delete_category(
        self,
        *,
        category_id: UUID,
    ) -> UUID:
        """Удаляет категорию."""
        return await self.manager.delete_category(
            category_id=category_id,
        )

    async def list_documents(
        self,
        *,
        section_id: UUID,
    ) -> tuple[
        NormativeDocumentView,
        ...,
    ]:
        """Возвращает документы раздела."""
        return await self.manager.list_documents(
            section_id=section_id,
        )

    async def upload_document(
        self,
        *,
        section_id: UUID,
        category_id: UUID | None,
        original_name: str,
        content: bytes,
        content_type: str,
    ) -> NormativeDocumentView:
        """Загружает managed PDF."""
        return await self.manager.upload_document(
            section_id=section_id,
            category_id=category_id,
            original_name=original_name,
            content=content,
            content_type=content_type,
        )

    async def get_document(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocumentView:
        """Возвращает document metadata."""
        return await self.manager.get_document(
            document_id=document_id,
        )

    async def move_document(
        self,
        *,
        document_id: UUID,
        category_id: UUID | None,
    ) -> NormativeDocumentView:
        """Перемещает document."""
        return await self.manager.move_document(
            document_id=document_id,
            category_id=category_id,
        )

    async def delete_document(
        self,
        *,
        document_id: UUID,
    ) -> UUID:
        """Удаляет document."""
        return await self.manager.delete_document(
            document_id=document_id,
        )

    async def queue_document(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocumentView:
        """Запускает durable индексацию."""
        return await self.manager.queue_document(
            document_id=document_id,
        )

    async def get_document_content(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocumentContent:
        """Возвращает PDF."""
        return await self.manager.get_document_content(
            document_id=document_id,
        )
