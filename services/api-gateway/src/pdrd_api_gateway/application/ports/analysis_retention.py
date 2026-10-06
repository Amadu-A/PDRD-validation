# services/api-gateway/src/pdrd_api_gateway/application/ports/analysis_retention.py

"""Границы очистки артефактов и копии исходного ТЗ во внешнем сервисе."""

from typing import Protocol
from uuid import UUID

from pdrd_api_gateway.domain.normative_snapshot import TechnicalAssignmentSnapshot


class AnalysisRetentionArtifacts(Protocol):
    """Удаляет исходники отдельно от бессрочных JSON результатов."""

    async def remove_sources(self, *, document_id: UUID | None, job_id: UUID) -> None:
        """Удаляет PDF/CAD/ТЗ, визуализацию и кэш экспортов, сохраняя JSON."""
        ...

    async def remove_guest(self, *, document_id: UUID | None, job_id: UUID) -> None:
        """Идемпотентно удаляет все файловые артефакты гостевого задания."""
        ...


class TechnicalAssignmentRetention(Protocol):
    """Очищает копию исходного ТЗ через контракт Knowledge Service."""

    async def remove_source(
        self,
        snapshot: TechnicalAssignmentSnapshot,
        *,
        purge_metadata: bool,
    ) -> None:
        """Удаляет исходник ТЗ, не изменяя нормативные документы."""
        ...
