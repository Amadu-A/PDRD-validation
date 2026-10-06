# services/knowledge-service/src/pdrd_knowledge_service/application/ports/section_locks.py

"""Межпроцессные блокировки раздела для индексации и очистки хранилищ."""

from contextlib import AbstractAsyncContextManager, nullcontext
from typing import Protocol
from uuid import UUID


class CatalogSectionLocks(Protocol):
    """Согласует операции раздела между HTTP процессом и индексаторами."""

    def shared(self, section_id: UUID) -> AbstractAsyncContextManager[None]:
        """Допускает параллельные операции, запрещая удаление раздела."""
        ...

    def exclusive(self, section_id: UUID) -> AbstractAsyncContextManager[None]:
        """Ждёт текущие операции и запрещает новые на время удаления."""
        ...


class InMemoryCatalogSectionLocks:
    """Пустая реализация для изолированных сценариев без внешних процессов."""

    def shared(self, section_id: UUID) -> AbstractAsyncContextManager[None]:
        """Возвращает пустую асинхронную область."""
        return nullcontext()

    def exclusive(self, section_id: UUID) -> AbstractAsyncContextManager[None]:
        """Возвращает пустую асинхронную область."""
        return nullcontext()
