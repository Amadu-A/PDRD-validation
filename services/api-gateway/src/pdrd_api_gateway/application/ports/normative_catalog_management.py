# services/api-gateway/src/pdrd_api_gateway/application/ports/normative_catalog_management.py

"""Application port управления managed normative catalog."""

from collections.abc import (
    Mapping,
)
from dataclasses import dataclass
from datetime import datetime
from typing import (
    Literal,
    Protocol,
)
from uuid import UUID

NormativeIndexingStatus = Literal[
    "uploaded",
    "queued",
    "indexing",
    "ready",
    "failed",
    "deleting",
]


class NormativeCatalogManagementError(RuntimeError):
    """Базовая ошибка Gateway facade нормативного каталога."""


class NormativeCatalogValidationError(
    NormativeCatalogManagementError,
):
    """Knowledge Service отклонил входные данные."""


class NormativeCatalogNotFoundError(
    NormativeCatalogManagementError,
):
    """Запрошенная сущность каталога не найдена."""


class NormativeCatalogConflictError(
    NormativeCatalogManagementError,
):
    """Операция конфликтует с lifecycle сущности."""


class NormativeCatalogUnavailableError(
    NormativeCatalogManagementError,
):
    """Knowledge Service или его dependency временно недоступны."""


class NormativeCatalogProtocolError(
    NormativeCatalogUnavailableError,
):
    """Knowledge Service вернул неожиданный transport payload."""


@dataclass(frozen=True, slots=True)
class NormativeSectionView:
    """Публичное представление нормативного раздела."""

    section_id: UUID

    name: str

    system_prompt: str

    created_at: datetime

    updated_at: datetime


@dataclass(frozen=True, slots=True)
class NormativeCategoryView:
    """Публичное представление категории нормативов."""

    category_id: UUID

    section_id: UUID

    parent_id: UUID | None

    name: str

    created_at: datetime

    updated_at: datetime


@dataclass(frozen=True, slots=True)
class NormativeDocumentView:
    """Публичная metadata managed нормативного документа."""

    document_id: UUID

    section_id: UUID

    category_id: UUID | None

    original_name: str

    mime_type: str

    size_bytes: int

    index_status: NormativeIndexingStatus

    index_error: str | None

    indexed_at: datetime | None

    ready_for_analysis: bool

    created_at: datetime

    updated_at: datetime


@dataclass(frozen=True, slots=True)
class NormativeDocumentContent:
    """Бинарное содержимое нормативного документа."""

    content: bytes

    mime_type: str


class NormativeCatalogManager(Protocol):
    """Контракт полного управления normative catalog."""

    async def list_sections(
        self,
    ) -> tuple[
        NormativeSectionView,
        ...,
    ]:
        """Возвращает все нормативные разделы."""
        ...

    async def create_section(
        self,
        *,
        name: str,
    ) -> NormativeSectionView:
        """Создаёт новый нормативный раздел."""
        ...

    async def get_section(
        self,
        *,
        section_id: UUID,
    ) -> NormativeSectionView:
        """Возвращает один раздел."""
        ...

    async def update_section(
        self,
        *,
        section_id: UUID,
        changes: Mapping[
            str,
            object,
        ],
    ) -> NormativeSectionView:
        """Изменяет имя и/или system prompt."""
        ...

    async def delete_section(
        self,
        *,
        section_id: UUID,
    ) -> UUID:
        """Удаляет пустой нормативный раздел."""
        ...

    async def list_categories(
        self,
        *,
        section_id: UUID,
    ) -> tuple[
        NormativeCategoryView,
        ...,
    ]:
        """Возвращает категории раздела."""
        ...

    async def create_category(
        self,
        *,
        section_id: UUID,
        name: str,
        parent_id: UUID | None,
    ) -> NormativeCategoryView:
        """Создаёт категорию."""
        ...

    async def get_category(
        self,
        *,
        category_id: UUID,
    ) -> NormativeCategoryView:
        """Возвращает одну категорию."""
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
        """Переименовывает или перемещает категорию."""
        ...

    async def delete_category(
        self,
        *,
        category_id: UUID,
    ) -> UUID:
        """Удаляет категорию."""
        ...

    async def list_documents(
        self,
        *,
        section_id: UUID,
    ) -> tuple[
        NormativeDocumentView,
        ...,
    ]:
        """Возвращает документы раздела."""
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
        """Загружает PDF через Knowledge Service."""
        ...

    async def get_document(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocumentView:
        """Возвращает metadata документа."""
        ...

    async def move_document(
        self,
        *,
        document_id: UUID,
        category_id: UUID | None,
    ) -> NormativeDocumentView:
        """Перемещает document в category или root."""
        ...

    async def delete_document(
        self,
        *,
        document_id: UUID,
    ) -> UUID:
        """Удаляет document по managed lifecycle."""
        ...

    async def queue_document(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocumentView:
        """Ставит document в durable indexing queue."""
        ...

    async def get_document_content(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocumentContent:
        """Возвращает бинарное содержимое документа."""
        ...
