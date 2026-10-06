# services/knowledge-service/src/pdrd_knowledge_service/application/ports/document_storage.py

"""Порт физического хранения нормативных документов."""

from typing import Protocol
from uuid import UUID


class NormativeDocumentStorageError(RuntimeError):
    """Ошибка физического storage нормативных документов."""


class NormativeDocumentStorageNotFoundError(
    NormativeDocumentStorageError,
):
    """Физический файл нормативного документа не найден."""


class NormativeDocumentStorage(Protocol):
    """Контракт физического хранения нормативных документов."""

    async def save(
        self,
        *,
        storage_key: str,
        content: bytes,
    ) -> None:
        """Сохраняет документ по внутреннему storage key."""
        ...

    async def read(
        self,
        *,
        storage_key: str,
    ) -> bytes:
        """Возвращает содержимое документа."""
        ...

    async def delete(
        self,
        *,
        storage_key: str,
    ) -> None:
        """Идемпотентно удаляет физический документ."""
        ...

    async def delete_section(self, *, section_id: UUID) -> None:
        """Удаляет папку одного раздела и осиротевшие артефакты внутри неё."""
        ...
