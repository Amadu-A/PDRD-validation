# services/knowledge-service/src/pdrd_knowledge_service/application/ports/technical_assignment_requirement_reader.py

"""Application port чтения atomic requirements ТЗ."""

from typing import Protocol
from uuid import UUID

from pdrd_knowledge_service.domain.technical_assignment_requirements import (
    TechnicalAssignmentRequirementSource,
)


class TechnicalAssignmentRequirementReaderError(
    RuntimeError,
):
    """Ошибка чтения atomic requirements из T index."""


class TechnicalAssignmentRequirementReader(
    Protocol,
):
    """Контракт filtered read atomic T requirements."""

    async def list_requirements(
        self,
        *,
        technical_assignment_id: UUID,
        analysis_document_id: UUID,
        section_id: UUID,
    ) -> tuple[
        TechnicalAssignmentRequirementSource,
        ...,
    ]:
        """Возвращает все requirement_text points одного immutable ТЗ."""
        ...
