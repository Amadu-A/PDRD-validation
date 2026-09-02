# services/analysis-service/src/pdrd_analysis_service/transport/http/project_context_schemas.py

"""HTTP schemas Analysis Project Context API."""

from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from pdrd_analysis_service.domain.project_context import (
    ProjectContextPage,
    ProjectContextSource,
)
from pdrd_analysis_service.transport.http.schemas import (
    PageFactsPayload,
)


class ProjectContextPagePayload(BaseModel):
    """Text-only страница предполагаемой ПЗ."""

    model_config = ConfigDict(
        extra="forbid",
    )

    page_number: int = Field(
        ge=1,
    )

    text: str

    def to_domain(
        self,
    ) -> ProjectContextPage:
        """Преобразует HTTP payload в Domain."""
        return ProjectContextPage(
            page_number=self.page_number,
            text=self.text,
        )


class ProjectContextClassificationPayload(BaseModel):
    """Классификация одной страницы."""

    page_number: int

    kind: str

    confidence: float

    reason: str


class ValidateProjectContextRequest(BaseModel):
    """Запрос проверки диапазона ПЗ."""

    model_config = ConfigDict(
        extra="forbid",
    )

    enabled: bool = False

    pages: list[ProjectContextPagePayload] = Field(
        default_factory=list,
    )


class ValidateProjectContextResponse(BaseModel):
    """Результат проверки диапазона ПЗ."""

    enabled: bool

    pages_count: int

    classifications: list[ProjectContextClassificationPayload]

    warnings: list[ProjectContextClassificationPayload]

    metrics: list[dict[str, Any]]


class BuildProjectContextQueryRequest(BaseModel):
    """Запрос semantic query по текущему листу."""

    model_config = ConfigDict(
        extra="forbid",
    )

    page_facts: PageFactsPayload

    extracted_text: str


class BuildProjectContextQueryResponse(BaseModel):
    """Semantic query для Knowledge Service."""

    query: str


class ProjectContextSourcePayload(BaseModel):
    """Retrieved Project Context source."""

    model_config = ConfigDict(
        extra="forbid",
    )

    source_id: str

    point_id: str | None = None

    score: float

    page: int | None = None

    chunk_index: int | None = None

    text: str

    def to_domain(
        self,
    ) -> ProjectContextSource:
        """Преобразует wire source в Domain."""
        return ProjectContextSource(
            source_id=self.source_id,
            score=self.score,
            page=self.page,
            chunk_index=self.chunk_index,
            text=self.text,
        )


class ProjectContextSourceExcerptPayload(BaseModel):
    """Компактный Project Context source для result."""

    source_id: str

    score: float

    page: int | None = None

    chunk_index: int | None = None

    text_excerpt: str


class AugmentProjectContextRequest(BaseModel):
    """Запрос добавления PZ sources к analysis text."""

    model_config = ConfigDict(
        extra="forbid",
    )

    extracted_text: str

    sources: list[ProjectContextSourcePayload] = Field(
        default_factory=list,
    )


class AugmentProjectContextResponse(BaseModel):
    """Текст и hints после Project Context augmentation."""

    analysis_text: str

    project_context_texts: list[str]

    sources: list[ProjectContextSourceExcerptPayload]
