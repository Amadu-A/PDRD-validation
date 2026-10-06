# services/api-gateway/src/pdrd_api_gateway/application/ports/user_package_catalog.py

"""Порт управления пользовательскими пакетами."""

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
    """Контракт управления личными пакетами каталога."""

    def for_owner(self, owner_user_id: UUID) -> "UserPackageCatalogManager":
        """Создаёт изолированный от других запросов каталог указанного владельца."""
        ...

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
        """Возвращает документ личного пакетаs раздела."""
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
        """Загружает PDF/DOC/DOCX в область личных пакетов."""
        ...

    async def get_document(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocumentView:
        """Возвращает документ личного пакета."""
        ...

    async def move_document(
        self,
        *,
        document_id: UUID,
        category_id: UUID | None,
    ) -> NormativeDocumentView:
        """Перемещает документ личного пакета."""
        ...

    async def delete_document(
        self,
        *,
        document_id: UUID,
    ) -> UUID:
        """Удаляет документ личного пакета."""
        ...

    async def queue_document(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocumentView:
        """Ставит документ личного пакета в очередь индексации."""
        ...

    async def get_document_content(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocumentContent:
        """Возвращает содержимое документа для просмотра в браузере."""
        ...
