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


class TechnicalAssignmentGuidedSearchRequest(
    BaseModel,
):
    """Запрос T-guided нормативного retrieval."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    queries: list[str]

    technical_assignment_id: UUID

    section_id: UUID

    normative_document_ids: list[UUID]


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


class TechnicalAssignmentSourceResponse(
    BaseModel,
):
    """Страница ТЗ из multimodal retrieval."""

    model_config = ConfigDict(
        frozen=True,
    )

    source_id: str

    point_id: str

    score: float

    technical_assignment_id: str | None

    analysis_document_id: str | None

    section_id: str | None

    source_sha256: str | None

    source_file: str | None

    page: int | str | None

    text: str

    normative_refs: list[str]


class NormativeReferenceResolutionResponse(
    BaseModel,
):
    """Результат разрешения N reference из ТЗ."""

    model_config = ConfigDict(
        frozen=True,
    )

    reference: str

    normalized_reference: str

    status: str

    document_ids: list[str]

    source_files: list[str]


class RetrievalDiagnosticResponse(
    BaseModel,
):
    """Негаллюцинаторная retrieval diagnostic."""

    model_config = ConfigDict(
        frozen=True,
    )

    code: str

    message: str

    source_id: str | None

    reference: str | None


class TechnicalAssignmentConflictCandidateResponse(
    BaseModel,
):
    """T/N pair для semantic conflict validation."""

    model_config = ConfigDict(
        frozen=True,
    )

    technical_assignment_source_id: str

    normative_source_ids: list[str]

    reason: str


class NormativeSearchResponse(BaseModel):
    """Ответ нормативного поиска."""

    model_config = ConfigDict(
        frozen=True,
    )

    queries: list[str]

    sources: list[NormativeSourceResponse]

    embedding_model: str


class NormativeGroupedSearchItemResponse(BaseModel):
    """Нормативный retrieval для одного исходного query."""

    model_config = ConfigDict(
        frozen=True,
    )

    query: str

    sources: list[NormativeSourceResponse]

    embedding_model: str


class NormativeGroupedSearchResponse(BaseModel):
    """Нормативный retrieval без смешивания результатов разных queries."""

    model_config = ConfigDict(
        frozen=True,
    )

    results: list[NormativeGroupedSearchItemResponse]


class UserPackageSearchResponse(BaseModel):
    """Ответ retrieval пользовательских документов."""

    model_config = ConfigDict(
        frozen=True,
    )

    queries: list[str]

    sources: list[UserPackageSourceResponse]

    embedding_model: str


class TechnicalAssignmentGuidedSearchResponse(
    BaseModel,
):
    """Ответ separate-budget T-guided retrieval."""

    model_config = ConfigDict(
        frozen=True,
    )

    queries: list[str]

    technical_assignment_sources: list[TechnicalAssignmentSourceResponse]

    reference_resolutions: list[NormativeReferenceResolutionResponse]

    targeted_normative_sources: list[NormativeSourceResponse]

    general_normative_sources: list[NormativeSourceResponse]

    normative_sources: list[NormativeSourceResponse]

    diagnostics: list[RetrievalDiagnosticResponse]

    conflict_candidates: list[TechnicalAssignmentConflictCandidateResponse]

    technical_assignment_embedding_model: str

    normative_embedding_model: str


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
