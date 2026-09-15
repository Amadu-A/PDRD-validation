# services/knowledge-service/src/pdrd_knowledge_service/transport/http/schemas/project_context.py

"""HTTP schemas reusable Project Context."""

from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from pdrd_knowledge_service.domain.project_context import (
    ProjectContextTextPage,
    ProjectContextValidationItem,
    ProjectContextValidationSnapshot,
)


class ProjectContextTextPagePayload(BaseModel):
    """Text-only страница ПЗ."""

    model_config = ConfigDict(
        extra="forbid",
    )

    page_number: int = Field(
        ge=1,
    )

    text: str

    def to_domain(
        self,
    ) -> ProjectContextTextPage:
        """Преобразует payload в Domain."""
        return ProjectContextTextPage(
            page_number=self.page_number,
            text=self.text,
        )


class ProjectContextValidationItemPayload(BaseModel):
    """Сохранённая классификация одной страницы ПЗ."""

    model_config = ConfigDict(
        extra="forbid",
    )

    page_number: int = Field(
        ge=1,
    )

    kind: str

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    reason: str

    def to_domain(
        self,
    ) -> ProjectContextValidationItem:
        """Преобразует validation item в Domain."""
        return ProjectContextValidationItem(
            page_number=self.page_number,
            kind=self.kind,
            confidence=self.confidence,
            reason=self.reason,
        )


class ProjectContextValidationSnapshotPayload(BaseModel):
    """Persisted результат VLM validation ПЗ."""

    model_config = ConfigDict(
        extra="forbid",
    )

    enabled: bool

    pages_count: int = Field(
        ge=0,
    )

    classifications: list[ProjectContextValidationItemPayload] = Field(
        default_factory=list,
    )

    warnings: list[ProjectContextValidationItemPayload] = Field(
        default_factory=list,
    )

    def to_domain(
        self,
    ) -> ProjectContextValidationSnapshot:
        """Преобразует HTTP snapshot в Domain."""
        return ProjectContextValidationSnapshot(
            enabled=self.enabled,
            pages_count=self.pages_count,
            classifications=tuple(item.to_domain() for item in self.classifications),
            warnings=tuple(item.to_domain() for item in self.warnings),
        )

    @classmethod
    def from_domain(
        cls,
        validation: ProjectContextValidationSnapshot,
    ) -> "ProjectContextValidationSnapshotPayload":
        """Создаёт HTTP payload из Domain validation."""
        return cls(
            enabled=validation.enabled,
            pages_count=validation.pages_count,
            classifications=[
                ProjectContextValidationItemPayload(
                    page_number=item.page_number,
                    kind=item.kind,
                    confidence=item.confidence,
                    reason=item.reason,
                )
                for item in validation.classifications
            ],
            warnings=[
                ProjectContextValidationItemPayload(
                    page_number=item.page_number,
                    kind=item.kind,
                    confidence=item.confidence,
                    reason=item.reason,
                )
                for item in validation.warnings
            ],
        )


class ResolveProjectContextCacheRequest(BaseModel):
    """Запрос проверки reusable PZ cache."""

    model_config = ConfigDict(
        extra="forbid",
    )

    context_id: UUID

    enabled: bool = False

    pages: list[ProjectContextTextPagePayload] = Field(
        default_factory=list,
    )


class ResolveProjectContextCacheResponse(BaseModel):
    """Результат разрешения reusable PZ cache."""

    context_id: UUID

    enabled: bool

    cache_key: str | None

    cache_hit: bool

    collection_name: str | None

    pages_count: int

    chunks_count: int

    vector_size: int

    validation: ProjectContextValidationSnapshotPayload | None = None


class CreateProjectContextRequest(BaseModel):
    """Запрос создания либо переиспользования PZ cache."""

    model_config = ConfigDict(
        extra="forbid",
    )

    context_id: UUID

    enabled: bool = False

    cache_key: str | None = None

    validation: ProjectContextValidationSnapshotPayload | None = None

    pages: list[ProjectContextTextPagePayload] = Field(
        default_factory=list,
    )


class CreateProjectContextResponse(BaseModel):
    """Результат создания либо reuse PZ cache."""

    context_id: UUID

    enabled: bool

    collection_name: str | None

    pages_count: int

    chunks_count: int

    vector_size: int

    cache_key: str | None

    cache_hit: bool

    validation: ProjectContextValidationSnapshotPayload | None = None


class SearchProjectContextRequest(BaseModel):
    """Запрос semantic retrieval по ПЗ."""

    model_config = ConfigDict(
        extra="forbid",
    )

    context_id: UUID

    enabled: bool = False

    query: str = ""


class ProjectContextSourcePayload(BaseModel):
    """Retrieved PZ source."""

    source_id: str

    point_id: str

    score: float

    page: int | None = None

    chunk_index: int | None = None

    text: str


class SearchProjectContextResponse(BaseModel):
    """Результат semantic retrieval по ПЗ."""

    context_id: UUID

    query: str

    sources: list[ProjectContextSourcePayload]

    embedding_model: str


class DeleteProjectContextResponse(BaseModel):
    """Результат явного удаления cache."""

    context_id: UUID

    deleted: bool
