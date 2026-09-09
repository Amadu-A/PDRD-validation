# services/api-gateway/src/pdrd_api_gateway/application/ports/user_package_catalog.py

"""Application port управления пользовательскими пакетами."""

from collections.abc import (
    Mapping,
)
from typing import Protocol
from uuid import UUID

from pdrd_api_gateway.application.ports.normative_catalog_management import (
    NormativeCategoryView,
    NormativeDocumentContent,
    NormativeDocumentView,
)


class UserPackageCatalogManager(Protocol):
    """Контракт управления user-package областью managed catalog."""

    async def list_categories(
        self,
        *,
        section_id: UUID,
    ) -> tuple[
        NormativeCategoryView,
        ...,
    ]:
        """Возвращает package folders раздела."""
        ...

    async def create_category(
        self,
        *,
        section_id: UUID,
        name: str,
        parent_id: UUID | None,
    ) -> NormativeCategoryView:
        """Создаёт package folder."""
        ...

    async def get_category(
        self,
        *,
        category_id: UUID,
    ) -> NormativeCategoryView:
        """Возвращает package folder."""
        ...

    async def update_category(
        self,
        *,
        category_id: UUID,
        changes: Mapping[
            str,
            object,
        ],
    ) -> NormativeCategoryView:
        """Переименовывает или перемещает package folder."""
        ...

    async def delete_category(
        self,
        *,
        category_id: UUID,
    ) -> UUID:
        """Удаляет package folder."""
        ...

    async def list_documents(
        self,
        *,
        section_id: UUID,
    ) -> tuple[
        NormativeDocumentView,
        ...,
    ]:
        """Возвращает user-package documents раздела."""
        ...

    async def upload_document(
        self,
        *,
        section_id: UUID,
        category_id: UUID | None,
        original_name: str,
        content: bytes,
        content_type: str,
    ) -> NormativeDocumentView:
        """Загружает PDF/DOC/DOCX в user-package area."""
        ...

    async def get_document(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocumentView:
        """Возвращает package document."""
        ...

    async def move_document(
        self,
        *,
        document_id: UUID,
        category_id: UUID | None,
    ) -> NormativeDocumentView:
        """Перемещает package document."""
        ...

    async def delete_document(
        self,
        *,
        document_id: UUID,
    ) -> UUID:
        """Удаляет package document."""
        ...

    async def queue_document(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocumentView:
        """Ставит package document в indexing queue."""
        ...

    async def get_document_content(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocumentContent:
        """Возвращает browser-viewable document content."""
        ...
