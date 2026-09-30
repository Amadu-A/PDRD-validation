# services/knowledge-service/src/pdrd_knowledge_service/application/ports/experience_feed.py

"""Knowledge читает Experience через порт, без доступа к чужой PostgreSQL-схеме."""

from typing import Protocol
from uuid import UUID

from pdrd_knowledge_service.domain.experience_index import (
    ExampleReference,
    TrustedExample,
)


class ExperienceFeedError(RuntimeError):
    """Закрытый источник E недоступен либо нарушил версионированный контракт."""


class ExperienceFeed(Protocol):
    """Читает страницы, проверяет редакции и получает PNG только через владельца данных."""

    async def page(
        self, *, after: UUID | None, limit: int
    ) -> tuple[tuple[TrustedExample, ...], UUID | None]:
        """Возвращает ограниченную страницу и курсор исходного каталога."""
        ...

    async def verify(
        self, references: tuple[ExampleReference, ...]
    ) -> tuple[TrustedExample, ...]:
        """Повторно проверяет актуальность после embedding и непосредственно перед выдачей E."""
        ...

    async def crop(self, *, example: TrustedExample, index: int) -> bytes:
        """Читает PNG точной редакции и проверяет SHA-256."""
        ...
