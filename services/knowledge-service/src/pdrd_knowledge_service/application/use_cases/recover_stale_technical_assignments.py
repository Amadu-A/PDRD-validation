# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/recover_stale_technical_assignments.py

"""Reconciliation зависшего T-indexing после worker loss/redeploy."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import (
    UTC,
    datetime,
    timedelta,
)
from uuid import uuid4

from pdrd_knowledge_service.application.ports.technical_assignment_persistence import (
    TechnicalAssignmentUnitOfWorkFactory,
)
from pdrd_knowledge_service.domain.technical_assignment import (
    TechnicalAssignmentIndexStatus,
)
from pdrd_knowledge_service.domain.technical_assignment_outbox import (
    TechnicalAssignmentOutboxMessage,
)

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    """Возвращает UTC now."""
    return datetime.now(
        UTC,
    )


@dataclass(frozen=True, slots=True)
class TechnicalAssignmentRecoveryReport:
    """Результат одного T reconciliation прохода."""

    selected: int
    requeued: int
    failed: int


@dataclass(frozen=True, slots=True)
class RecoverStaleTechnicalAssignments:
    """Возвращает stale INDEXING в очередь и закрывает истёкшие ТЗ."""

    unit_of_work_factory: TechnicalAssignmentUnitOfWorkFactory
    max_runtime_seconds: int
    stale_indexing_seconds: int
    clock: Clock = utc_now

    async def execute(
        self,
        *,
        limit: int,
    ) -> TechnicalAssignmentRecoveryReport:
        """Выполняет bounded reconciliation одной batch."""
        now = self.clock()

        stale_before = now - timedelta(
            seconds=self.stale_indexing_seconds,
        )

        deadline_before = now - timedelta(
            seconds=self.max_runtime_seconds,
        )

        async with self.unit_of_work_factory() as unit_of_work:
            assignments = await unit_of_work.assignments.get_recoverable(
                stale_indexing_before=stale_before,
                deadline_before=deadline_before,
                limit=limit,
            )

            requeued = 0
            failed = 0

            for assignment in assignments:
                runtime_seconds = (now - assignment.created_at).total_seconds()

                if runtime_seconds >= self.max_runtime_seconds:
                    changed = assignment.transition_indexing(
                        target_status=TechnicalAssignmentIndexStatus.FAILED,
                        changed_at=now,
                        error=("T-indexing превысил абсолютный lifecycle deadline."),
                    )
                    failed += 1

                elif assignment.index_status is TechnicalAssignmentIndexStatus.INDEXING:
                    changed = assignment.transition_indexing(
                        target_status=TechnicalAssignmentIndexStatus.QUEUED,
                        changed_at=now,
                    )

                    await unit_of_work.outbox.add(
                        TechnicalAssignmentOutboxMessage.index_requested(
                            message_id=uuid4(),
                            technical_assignment_id=(
                                assignment.technical_assignment_id
                            ),
                            created_at=now,
                        )
                    )

                    requeued += 1

                else:
                    continue

                await unit_of_work.assignments.update(
                    changed,
                )

            if assignments:
                await unit_of_work.commit()

        return TechnicalAssignmentRecoveryReport(
            selected=len(assignments),
            requeued=requeued,
            failed=failed,
        )
