# services/knowledge-service/src/pdrd_knowledge_service/transport/http/schemas/search.py

"""HTTP schemas поиска по базе знаний."""

from typing import Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    model_validator,
)


class NormativeSearchRequest(BaseModel):
    """Запрос нормативного поиска."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    queries: list[str]

    section_id: UUID | None = None

    document_ids: list[UUID] | None = None

    @model_validator(
        mode="after",
    )
    def validate_scope(
        self,
    ) -> Self:
        """Требует section_id и document_ids только совместно."""
        if (self.section_id is None) != (self.document_ids is None):
            raise ValueError(
                "section_id и document_ids должны передаваться вместе.",
            )

        return self


class UserPackageSearchRequest(BaseModel):
    """Запрос retrieval выбранных пользовательских документов."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    queries: list[str]

    section_id: UUID | None = None

    document_ids: list[UUID] | None = None

    @model_validator(
        mode="after",
    )
    def validate_scope(
        self,
    ) -> Self:
        """Запрещает частично заданный package scope."""
        if (self.section_id is None) != (self.document_ids is None):
            raise ValueError(
                "section_id и document_ids должны передаваться вместе.",
            )

        return self


class ExperienceSearchRequest(BaseModel):
    """Запрос поиска по Базе Опыта."""

    model_config = ConfigDict(
        frozen=True,
    )

    queries: list[str]


class NormativeSourceResponse(BaseModel):
    """Нормативный источник."""

    model_config = ConfigDict(
        frozen=True,
    )

    source_id: str
    point_id: str
    score: float

    document_id: str | None
    section_id: str | None
    category_id: str | None

    source_sha256: str | None

    source_file: str | None
    source_path: str | None

    page: int | str | None
    chunk_index: int | str | None

    text: str


class UserPackageSourceResponse(BaseModel):
    """Источник из выбранного пользовательского документа."""

    model_config = ConfigDict(
        frozen=True,
    )

    source_id: str
    point_id: str
    score: float

    document_id: str | None
    section_id: str | None
    category_id: str | None

    source_sha256: str | None

    source_file: str | None
    source_path: str | None

    page: int | str | None
    chunk_index: int | str | None

    text: str


class NormativeSearchResponse(BaseModel):
    """Ответ нормативного поиска."""

    model_config = ConfigDict(
        frozen=True,
    )

    queries: list[str]

    sources: list[NormativeSourceResponse]

    embedding_model: str


class UserPackageSearchResponse(BaseModel):
    """Ответ retrieval пользовательских документов."""

    model_config = ConfigDict(
        frozen=True,
    )

    queries: list[str]

    sources: list[UserPackageSourceResponse]

    embedding_model: str


class ExperienceSourceResponse(BaseModel):
    """Источник из Базы Опыта."""

    model_config = ConfigDict(
        frozen=True,
    )

    source_id: str
    point_id: str
    score: float

    project_id: str | None
    issue_id: str | None
    issue_text: str | None
    status: str | None

    verified_fixed: bool

    before_page: int | str | None
    after_page: int | str | None

    before_context: str
    after_context: str


class ExperienceSearchItemResponse(BaseModel):
    """Результат поиска для одного нарушения."""

    model_config = ConfigDict(
        frozen=True,
    )

    query: str

    sources: list[ExperienceSourceResponse]

    embedding_model: str


class ExperienceSearchResponse(BaseModel):
    """Ответ поиска сразу для нескольких нарушений."""

    model_config = ConfigDict(
        frozen=True,
    )

    results: list[ExperienceSearchItemResponse]
