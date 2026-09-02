# services/knowledge-service/src/pdrd_knowledge_service/transport/http/schemas/project_context.py

"""HTTP schemas temporary Project Context."""

from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from pdrd_knowledge_service.domain.project_context import (
    ProjectContextTextPage,
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
            page_number=(self.page_number),
            text=self.text,
        )


class CreateProjectContextRequest(BaseModel):
    """Запрос временной индексации ПЗ."""

    model_config = ConfigDict(
        extra="forbid",
    )

    context_id: UUID

    enabled: bool = False

    pages: list[ProjectContextTextPagePayload] = Field(
        default_factory=list,
    )


class CreateProjectContextResponse(BaseModel):
    """Результат временной индексации ПЗ."""

    context_id: UUID

    enabled: bool

    collection_name: str | None

    pages_count: int

    chunks_count: int

    vector_size: int


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
    """Результат идемпотентного cleanup."""

    context_id: UUID

    deleted: bool
