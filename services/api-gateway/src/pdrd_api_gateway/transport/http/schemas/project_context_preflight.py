# services/api-gateway/src/pdrd_api_gateway/transport/http/schemas/project_context_preflight.py

"""HTTP schemas публичного preflight Project Context / ПЗ."""

from pydantic import (
    BaseModel,
    Field,
)


class ProjectContextPreflightWarningResponse(
    BaseModel,
):
    """Одна страница, которую автоматическая проверка считает подозрительной."""

    page_number: int = Field(
        ge=1,
    )

    kind: str

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    reason: str


class ProjectContextPreflightResponse(
    BaseModel,
):
    """Результат проверки ПЗ до создания длительного analysis job."""

    cache_hit: bool

    cache_built: bool

    pages_count: int = Field(
        ge=1,
    )

    requires_confirmation: bool

    warnings: list[ProjectContextPreflightWarningResponse] = Field(
        default_factory=list,
    )
