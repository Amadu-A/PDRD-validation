# services/knowledge-service/src/pdrd_knowledge_service/transport/http/schemas/normative_sections.py

"""HTTP schemas разделов нормативной базы."""

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
    NormativeSection,
)


class CreateNormativeSectionRequest(BaseModel):
    """Запрос создания раздела нормативной базы."""

    model_config = ConfigDict(
        extra="forbid",
    )

    name: str = Field(
        min_length=1,
        max_length=255,
    )


class UpdateNormativeSectionRequest(BaseModel):
    """Частичное изменение раздела нормативной базы."""

    model_config = ConfigDict(
        extra="forbid",
    )

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )

    system_prompt: str | None = None

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

        if "system_prompt" in fields and self.system_prompt is None:
            raise ValueError(
                "system_prompt не может быть null.",
            )

        return self


class NormativeSectionResponse(BaseModel):
    """Раздел нормативной базы."""

    model_config = ConfigDict(
        frozen=True,
    )

    section_id: UUID

    name: str

    system_prompt: str

    created_at: datetime

    updated_at: datetime

    @classmethod
    def from_domain(
        cls,
        section: NormativeSection,
    ) -> "NormativeSectionResponse":
        """Создаёт transport response из Domain entity."""
        return cls(
            section_id=section.section_id,
            name=section.name,
            system_prompt=section.system_prompt,
            created_at=section.created_at,
            updated_at=section.updated_at,
        )


class DeleteNormativeSectionResponse(BaseModel):
    """Результат удаления пустого раздела."""

    model_config = ConfigDict(
        frozen=True,
    )

    section_id: UUID

    deleted: bool = True
