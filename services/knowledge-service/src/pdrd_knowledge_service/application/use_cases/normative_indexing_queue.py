# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/normative_indexing_queue.py

"""Use case постановки нормативного документа в durable indexing queue."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import (
    UTC,
    datetime,
)
from uuid import (
    UUID,
    uuid4,
)

from pdrd_knowledge_service.application.ports.persistence import (
    NormativeCatalogUnitOfWorkFactory,
)
from pdrd_knowledge_service.application.use_cases.normative_documents import (
    NormativeDocumentNotFoundError,
)
from pdrd_knowledge_service.domain.normative_catalog import (
    IndexingStatus,
    NormativeDocument,
)
from pdrd_knowledge_service.domain.normative_outbox import (
    NormativeOutboxMessage,
)

Clock = Callable[
    [],
    datetime,
]

IdentifierFactory = Callable[
    [],
    UUID,
]

_QUEUEABLE_STATUSES = frozenset(
    {
        IndexingStatus.UPLOADED,
        IndexingStatus.FAILED,
        IndexingStatus.READY,
    }
)


class NormativeDocumentIndexingConflictError(RuntimeError):
    """Текущее состояние документа не допускает постановку в очередь."""


def utc_now() -> datetime:
    """Возвращает текущее timezone-aware UTC время."""
    return datetime.now(
        UTC,
    )


@dataclass(frozen=True, slots=True)
class QueueNormativeDocument:
    """Атомарно переводит документ в queued и создаёт outbox event."""

    unit_of_work_factory: NormativeCatalogUnitOfWorkFactory

    clock: Clock = utc_now

    identifier_factory: IdentifierFactory = uuid4

    async def execute(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocument:
        """Создаёт durable indexing request одной DB transaction."""
        async with self.unit_of_work_factory() as unit_of_work:
            document = await unit_of_work.documents.get_for_update(
                document_id,
            )

            if document is None:
                raise NormativeDocumentNotFoundError(
                    f"Нормативный документ {document_id} не найден.",
                )

            if document.index_status not in _QUEUEABLE_STATUSES:
                raise NormativeDocumentIndexingConflictError(
                    "Документ нельзя поставить в очередь индексации "
                    f"из состояния {document.index_status.value}.",
                )

            changed_at = self.clock()

            queued_document = document.transition_indexing(
                target_status=IndexingStatus.QUEUED,
                changed_at=changed_at,
            )

            message = NormativeOutboxMessage.index_requested(
                message_id=self.identifier_factory(),
                document_id=document_id,
                created_at=changed_at,
            )

            await unit_of_work.documents.update(
                queued_document,
            )

            await unit_of_work.outbox.add(
                message,
            )

            await unit_of_work.commit()

        return queued_document
