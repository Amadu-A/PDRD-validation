# services/user-service/src/pdrd_user_service/application/ports/section_catalog.py

"""Порт исходного каталога разделов для первого назначения доступа."""

from typing import Protocol
from uuid import UUID


class SectionCatalogUnavailable(Exception):
    """Каталог разделов не удалось получить без потери назначений."""


class SectionCatalog(Protocol):
    """Читает только актуальные UUID разделов из Knowledge Service."""

    async def list_section_ids(self) -> tuple[UUID, ...]:
        """Возвращает снимок разделов; сбой никогда не заменяется пустым списком."""
        ...
