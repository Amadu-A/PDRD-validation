# services/analysis-service/src/pdrd_analysis_service/application/use_cases/health.py

"""Readiness use case Analysis Service."""

from dataclasses import dataclass

from pdrd_analysis_service.application.ports.vision_model import (
    StructuredVisionModel,
)
from pdrd_analysis_service.domain.analysis import ReadinessReport


@dataclass(frozen=True, slots=True)
class CheckReadiness:
    """Проверяет readiness VLM provider."""

    vision_model: StructuredVisionModel

    async def execute(
        self,
    ) -> ReadinessReport:
        """Возвращает readiness report."""
        return ReadinessReport(vision_model=(await self.vision_model.is_ready()))
