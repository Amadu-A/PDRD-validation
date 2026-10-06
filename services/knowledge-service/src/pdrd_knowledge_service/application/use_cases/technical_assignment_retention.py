# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/technical_assignment_retention.py

"""Очищает отдельную копию ТЗ в её сервисе, сохраняя нормативный каталог."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID

from pdrd_knowledge_service.application.ports.document_storage import (
    NormativeDocumentStorage,
)
from pdrd_knowledge_service.application.ports.technical_assignment_persistence import (
    TechnicalAssignmentUnitOfWorkFactory,
)
from pdrd_knowledge_service.application.ports.vector_store import VectorStore
from pdrd_knowledge_service.application.use_cases.technical_assignments import (
    build_technical_assignment_storage_key,
)
from pdrd_knowledge_service.domain.technical_assignment import (
    TechnicalAssignmentIndexStatus,
)


class TechnicalAssignmentRetentionConflictError(ValueError):
    """Запрошено другое ТЗ или его индексация ещё выполняется."""


@dataclass(frozen=True, slots=True)
class CleanupTechnicalAssignment:
    """Не удаляет общую векторную коллекцию и чужие документы."""

    unit_of_work_factory: TechnicalAssignmentUnitOfWorkFactory
    storage: NormativeDocumentStorage
    vector_store: VectorStore
    collection: str

    async def execute(
        self,
        *,
        technical_assignment_id: UUID,
        analysis_document_id: UUID,
        sha256: str,
        purge_metadata: bool,
    ) -> None:
        """Проверяет идентичность, удаляет файл до фиксации; отсутствие идемпотентно."""
        async with self.unit_of_work_factory() as uow:
            assignment = await uow.assignments.get_for_update(technical_assignment_id)
            if assignment is None:
                return
            if (
                assignment.analysis_document_id != analysis_document_id
                or assignment.sha256 != sha256
                or assignment.index_status
                in {
                    TechnicalAssignmentIndexStatus.QUEUED,
                    TechnicalAssignmentIndexStatus.INDEXING,
                }
            ):
                raise TechnicalAssignmentRetentionConflictError(
                    "ТЗ изменилось или ещё индексируется."
                )
            key = build_technical_assignment_storage_key(
                analysis_document_id=assignment.analysis_document_id,
                technical_assignment_id=assignment.technical_assignment_id,
                original_name=assignment.original_name,
            )
            await self.storage.delete(storage_key=key)
            if purge_metadata:
                await self.vector_store.delete_by_filter(
                    collection=self.collection,
                    key="technical_assignment_id",
                    value=str(technical_assignment_id),
                )
                await uow.assignments.delete(technical_assignment_id)
            elif assignment.source_removed_at is None:
                await uow.assignments.update(
                    replace(assignment, source_removed_at=datetime.now(UTC))
                )
            await uow.commit()
