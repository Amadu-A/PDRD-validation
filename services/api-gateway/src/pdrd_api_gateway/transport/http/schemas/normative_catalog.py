# services/api-gateway/src/pdrd_api_gateway/transport/http/schemas/normative_catalog.py

"""Public HTTP schemas managed normative catalog."""

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from pdrd_api_gateway.application.ports.normative_catalog_management import (
    NormativeCategoryView,
    NormativeDocumentView,
    NormativeIndexingStatus,
    NormativeSectionView,
)


class CreateNormativeSectionRequest(BaseModel):
    """Запрос создания нормативного раздела."""

    model_config = ConfigDict(
        extra="forbid",
    )

    name: str = Field(
        min_length=1,
        max_length=255,
    )


class UpdateNormativeSectionRequest(BaseModel):
    """Частичное изменение раздела."""

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
        """Проверяет PATCH semantics."""
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
    """Публичное представление раздела."""

    model_config = ConfigDict(
        frozen=True,
    )

    section_id: UUID

    name: str

    system_prompt: str

    created_at: datetime

    updated_at: datetime

    @classmethod
    def from_view(
        cls,
        section: NormativeSectionView,
    ) -> "NormativeSectionResponse":
        """Создаёт response из application view."""
        return cls(
            section_id=section.section_id,
            name=section.name,
            system_prompt=section.system_prompt,
            created_at=section.created_at,
            updated_at=section.updated_at,
        )


class DeleteNormativeSectionResponse(BaseModel):
    """Результат удаления section."""

    model_config = ConfigDict(
        frozen=True,
    )

    section_id: UUID

    deleted: bool = True


class CreateNormativeCategoryRequest(BaseModel):
    """Запрос создания category."""

    model_config = ConfigDict(
        extra="forbid",
    )

    name: str = Field(
        min_length=1,
        max_length=255,
    )

    parent_id: UUID | None = None


class UpdateNormativeCategoryRequest(BaseModel):
    """PATCH категории."""

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
        """Проверяет PATCH semantics."""
        if not self.model_fields_set:
            raise ValueError(
                "Не передано ни одного поля для изменения.",
            )

        if "name" in self.model_fields_set and self.name is None:
            raise ValueError(
                "name не может быть null.",
            )

        return self


class NormativeCategoryResponse(BaseModel):
    """Публичное представление category."""

    model_config = ConfigDict(
        frozen=True,
    )

    category_id: UUID

    section_id: UUID

    parent_id: UUID | None

    name: str

    created_at: datetime

    updated_at: datetime

    @classmethod
    def from_view(
        cls,
        category: NormativeCategoryView,
    ) -> "NormativeCategoryResponse":
        """Создаёт response из application view."""
        return cls(
            category_id=category.category_id,
            section_id=category.section_id,
            parent_id=category.parent_id,
            name=category.name,
            created_at=category.created_at,
            updated_at=category.updated_at,
        )


class DeleteNormativeCategoryResponse(BaseModel):
    """Результат удаления category."""

    model_config = ConfigDict(
        frozen=True,
    )

    category_id: UUID

    deleted: bool = True


class MoveNormativeDocumentRequest(BaseModel):
    """Перемещение document в category или root."""

    model_config = ConfigDict(
        extra="forbid",
    )

    category_id: UUID | None


class NormativeDocumentResponse(BaseModel):
    """Публичная metadata нормативного документа."""

    model_config = ConfigDict(
        frozen=True,
    )

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

    @classmethod
    def from_view(
        cls,
        document: NormativeDocumentView,
    ) -> "NormativeDocumentResponse":
        """Создаёт response из application view."""
        return cls(
            document_id=document.document_id,
            section_id=document.section_id,
            category_id=document.category_id,
            original_name=document.original_name,
            mime_type=document.mime_type,
            size_bytes=document.size_bytes,
            index_status=document.index_status,
            index_error=document.index_error,
            indexed_at=document.indexed_at,
            ready_for_analysis=document.ready_for_analysis,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class DeleteNormativeDocumentResponse(BaseModel):
    """Результат удаления document."""

    model_config = ConfigDict(
        frozen=True,
    )

    document_id: UUID

    deleted: bool = True
