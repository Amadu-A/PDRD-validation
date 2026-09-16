# services/api-gateway/src/pdrd_api_gateway/application/ports/project_context_preflight.py

"""Application port preflight-проверки Project Context / ПЗ."""

from dataclasses import dataclass
from typing import Protocol


class ProjectContextPreflightError(RuntimeError):
    """Базовая ошибка preflight-проверки ПЗ."""


class InvalidProjectContextPreflightError(
    ProjectContextPreflightError,
):
    """Пользовательские данные ПЗ не прошли deterministic validation."""


class ProjectContextPreflightUnavailableError(
    ProjectContextPreflightError,
):
    """Один из внутренних сервисов preflight временно недоступен."""


@dataclass(frozen=True, slots=True)
class ProjectContextPreflightWarning:
    """Одна семантически подозрительная страница ПЗ."""

    page_number: int

    kind: str

    confidence: float

    reason: str


@dataclass(frozen=True, slots=True)
class ProjectContextPreflightResult:
    """Итог preflight и подготовки reusable Project Context cache."""

    cache_hit: bool

    cache_built: bool

    pages_count: int

    requires_confirmation: bool

    warnings: tuple[
        ProjectContextPreflightWarning,
        ...,
    ]


class ProjectContextPreflightCoordinator(Protocol):
    """Порт orchestration лёгкой проверки ПЗ до создания analysis job."""

    async def execute(
        self,
        *,
        pdf_content: bytes,
        file_name: str,
        start_page: int,
        end_page: int,
    ) -> ProjectContextPreflightResult:
        """Проверяет диапазон и гарантирует ready reusable cache."""
        ...
