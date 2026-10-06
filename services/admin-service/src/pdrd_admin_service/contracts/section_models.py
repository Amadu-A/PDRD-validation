# services/admin-service/src/pdrd_admin_service/contracts/section_models.py

"""Контракты живого каталога Knowledge и назначений разделов User Service."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CatalogSection(BaseModel):
    """Показывает UUID и название, не раскрывая содержимое системного промпта."""

    model_config = ConfigDict(extra="ignore")
    section_id: UUID
    name: str = Field(min_length=1)


class SectionAccessResponse(BaseModel):
    """Возвращает проверенный набор разделов одного профиля."""

    model_config = ConfigDict(extra="forbid")
    user_id: UUID
    section_ids: tuple[UUID, ...]
    all_sections: bool
    authorization_version: int = Field(ge=1)
