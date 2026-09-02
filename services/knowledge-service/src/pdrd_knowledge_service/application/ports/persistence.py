# services/knowledge-service/src/pdrd_knowledge_service/application/ports/persistence.py

"""Порты persistence нормативного каталога Knowledge Service."""

from collections.abc import Callable
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from pdrd_knowledge_service.domain.normative_catalog import (
    NormativeCategory,
    NormativeDocument,
    NormativeSection,
)


class NormativeSectionRepository(Protocol):
    """Контракт persistence операций над разделами нормативов."""

    async def add(
        self,
        section: NormativeSection,
    ) -> None:
        """Добавляет раздел в текущую transaction."""
        ...

    async def get(
        self,
        section_id: UUID,
    ) -> NormativeSection | None:
        """Возвращает раздел по идентификатору."""
        ...

    async def list_all(
        self,
    ) -> list[NormativeSection]:
        """Возвращает все разделы каталога."""
        ...

    async def update(
        self,
        section: NormativeSection,
    ) -> None:
        """Обновляет существующий раздел."""
        ...

    async def delete(
        self,
        section_id: UUID,
    ) -> None:
        """Удаляет раздел из persistence."""
        ...


class NormativeCategoryRepository(Protocol):
    """Контракт persistence операций над категориями."""

    async def add(
        self,
        category: NormativeCategory,
    ) -> None:
        """Добавляет категорию."""
        ...

    async def get(
        self,
        category_id: UUID,
    ) -> NormativeCategory | None:
        """Возвращает категорию по идентификатору."""
        ...

    async def list_by_section(
        self,
        section_id: UUID,
    ) -> list[NormativeCategory]:
        """Возвращает категории указанного раздела."""
        ...

    async def update(
        self,
        category: NormativeCategory,
    ) -> None:
        """Обновляет категорию."""
        ...

    async def delete(
        self,
        category_id: UUID,
    ) -> None:
        """Удаляет категорию."""
        ...


class NormativeDocumentRepository(Protocol):
    """Контракт persistence операций над нормативными документами."""

    async def add(
        self,
        document: NormativeDocument,
    ) -> None:
        """Добавляет metadata нормативного документа."""
        ...

    async def get(
        self,
        document_id: UUID,
    ) -> NormativeDocument | None:
        """Возвращает документ по идентификатору."""
        ...

    async def list_by_section(
        self,
        section_id: UUID,
    ) -> list[NormativeDocument]:
        """Возвращает документы указанного раздела."""
        ...

    async def update(
        self,
        document: NormativeDocument,
    ) -> None:
        """Обновляет metadata документа."""
        ...

    async def delete(
        self,
        document_id: UUID,
    ) -> None:
        """Удаляет metadata документа."""
        ...


class NormativeCatalogUnitOfWork(Protocol):
    """Транзакционная граница операций нормативного каталога."""

    sections: NormativeSectionRepository
    categories: NormativeCategoryRepository
    documents: NormativeDocumentRepository

    async def __aenter__(
        self,
    ) -> Self:
        """Открывает транзакционную область."""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Закрывает транзакционную область."""
        ...

    async def commit(
        self,
    ) -> None:
        """Фиксирует transaction."""
        ...

    async def rollback(
        self,
    ) -> None:
        """Откатывает transaction."""
        ...


NormativeCatalogUnitOfWorkFactory = Callable[
    [],
    NormativeCatalogUnitOfWork,
]
