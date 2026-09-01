# services/api-gateway/src/pdrd_api_gateway/application/ports/orchestration.py

"""Application port запуска orchestration анализа."""

from typing import Any, Protocol

from pdrd_api_gateway.application.ports.artifacts import (
    AnalysisRequestArtifacts,
)


class AnalysisOrchestrationError(RuntimeError):
    """Ошибка внешнего orchestration pipeline."""


class AnalysisOrchestrator(Protocol):
    """Контракт запуска сохранённой заявки через orchestration."""

    async def execute(
        self,
        *,
        artifacts: AnalysisRequestArtifacts,
    ) -> dict[str, Any]:
        """Выполняет анализ и возвращает итоговый JSON."""
        ...
