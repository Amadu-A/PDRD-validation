# services/user-service/src/pdrd_user_service/infrastructure/section_catalog.py

"""Читает исходный каталог по внутреннему HTTP без доступа к чужой БД."""

from uuid import UUID

import httpx
from pdrd_user_service.application.ports.section_catalog import (
    SectionCatalogUnavailable,
)
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError


class _CatalogSection(BaseModel):
    """Проверяет UUID, игнорируя неиспользуемые поля исходного каталога."""

    model_config = ConfigDict(extra="ignore")
    section_id: UUID
    deleting: bool = False


class KnowledgeSectionCatalog:
    """Реализует каталог для назначения разделов новому подтверждённому пользователю."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        """Принимает управляемый пул с фиксированным внутренним адресом."""
        self._client = client

    async def list_section_ids(self) -> tuple[UUID, ...]:
        """Отличает пустой настоящий каталог от сетевой ошибки или неверного ответа."""
        try:
            response = await self._client.get("/internal/v1/normative/sections")
            response.raise_for_status()
            sections = TypeAdapter(tuple[_CatalogSection, ...]).validate_python(
                response.json()
            )
        except (httpx.HTTPError, ValueError, ValidationError) as error:
            raise SectionCatalogUnavailable(
                "Каталог разделов временно недоступен"
            ) from error
        return tuple(section.section_id for section in sections if not section.deleting)
