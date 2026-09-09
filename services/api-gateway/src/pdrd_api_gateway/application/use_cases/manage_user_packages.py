# services/api-gateway/src/pdrd_api_gateway/application/use_cases/manage_user_packages.py

"""Application facade пользовательских пакетов документов."""

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from pdrd_api_gateway.application.ports.normative_catalog_management import (
    NormativeCategoryView,
    NormativeDocumentContent,
    NormativeDocumentView,
)
from pdrd_api_gateway.application.ports.user_package_catalog import (
    UserPackageCatalogManager,
)


@dataclass(frozen=True, slots=True)
class UserPackageCatalogFacade:
    """Предоставляет Transport use cases user-package catalog."""

    manager: UserPackageCatalogManager

    async def list_categories(
        self,
        *,
        section_id: UUID,
    ) -> tuple[
        NormativeCategoryView,
        ...,
    ]:
        """Возвращает package folders."""
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
        """Создаёт package folder."""
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
        """Возвращает package folder."""
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
        """Изменяет package folder."""
        return await self.manager.update_category(
            category_id=category_id,
            changes=changes,
        )

    async def delete_category(
        self,
        *,
        category_id: UUID,
    ) -> UUID:
        """Удаляет package folder."""
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
        """Возвращает package documents."""
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
        """Загружает package document."""
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
        """Возвращает package document."""
        return await self.manager.get_document(
            document_id=document_id,
        )

    async def move_document(
        self,
        *,
        document_id: UUID,
        category_id: UUID | None,
    ) -> NormativeDocumentView:
        """Перемещает package document."""
        return await self.manager.move_document(
            document_id=document_id,
            category_id=category_id,
        )

    async def delete_document(
        self,
        *,
        document_id: UUID,
    ) -> UUID:
        """Удаляет package document."""
        return await self.manager.delete_document(
            document_id=document_id,
        )

    async def queue_document(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocumentView:
        """Запускает durable indexing package document."""
        return await self.manager.queue_document(
            document_id=document_id,
        )

    async def get_document_content(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocumentContent:
        """Возвращает browser-viewable package document."""
        return await self.manager.get_document_content(
            document_id=document_id,
        )
