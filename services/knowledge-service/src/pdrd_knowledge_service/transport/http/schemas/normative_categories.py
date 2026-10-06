# services/knowledge-service/src/pdrd_knowledge_service/transport/http/schemas/normative_categories.py

"""HTTP-схемы категорий управляемого каталога."""

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from pdrd_knowledge_service.domain.normative_catalog import (
    CatalogArea,
    NormativeCategory,
)


class CreateNormativeCategoryRequest(BaseModel):
    """Запрос создания категории управляемого каталога."""

    model_config = ConfigDict(
        extra="forbid",
    )

    name: str = Field(
        min_length=1,
        max_length=255,
    )

    parent_id: UUID | None = None

    owner_user_id: UUID | None = None

    area: CatalogArea = CatalogArea.NORMATIVE


class UpdateNormativeCategoryRequest(BaseModel):
    """Частичное изменение категории управляемого каталога."""

    model_config = ConfigDict(
        extra="forbid",
    )

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )

    parent_id: UUID | None = None

    @model_validator(
        mode="after",
    )
    def validate_changes(
        self,
    ) -> Self:
        """Требует хотя бы одно явно переданное поле."""
        fields = self.model_fields_set

        if not fields:
            raise ValueError(
                "Не передано ни одного поля для изменения.",
            )

        if "name" in fields and self.name is None:
            raise ValueError(
                "name не может быть null.",
            )

        return self

    @property
    def changes_parent(
        self,
    ) -> bool:
        """Показывает, был ли parent_id явно передан клиентом."""
        return "parent_id" in self.model_fields_set


class NormativeCategoryResponse(BaseModel):
    """Категория управляемого каталога."""

    model_config = ConfigDict(
        frozen=True,
    )

    category_id: UUID

    section_id: UUID

    parent_id: UUID | None

    name: str

    owner_user_id: UUID | None = None

    area: CatalogArea

    created_at: datetime

    updated_at: datetime

    @classmethod
    def from_domain(
        cls,
        category: NormativeCategory,
    ) -> "NormativeCategoryResponse":
        """Создаёт HTTP-ответ из доменной сущности."""
        return cls(
            category_id=category.category_id,
            section_id=category.section_id,
            parent_id=category.parent_id,
            name=category.name,
            area=category.area,
            owner_user_id=category.owner_user_id,
            created_at=category.created_at,
            updated_at=category.updated_at,
        )


class DeleteNormativeCategoryResponse(BaseModel):
    """Результат удаления категории."""

    model_config = ConfigDict(
        frozen=True,
    )

    category_id: UUID

    deleted: bool = True
