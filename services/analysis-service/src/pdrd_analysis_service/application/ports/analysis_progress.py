# services/analysis-service/src/pdrd_analysis_service/application/ports/analysis_progress.py

"""Application port durable analysis progress/cancellation probe."""

from typing import Protocol
from uuid import UUID


class AnalysisProgressProbe(Protocol):
    """Проверяет durable cancellation между элементами длинного GPU stage."""

    async def is_cancelled(
        self,
        *,
        document_id: UUID,
        stage: str,
        current: int,
        total: int,
    ) -> bool:
        """Возвращает True, если analysis job уже отменён."""
        ...
