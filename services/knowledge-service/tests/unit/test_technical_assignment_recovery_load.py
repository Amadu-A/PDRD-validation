# services/knowledge-service/tests/unit/test_technical_assignment_recovery_load.py

"""Synthetic load test T-indexing reconciliation."""

from datetime import (
    UTC,
    datetime,
    timedelta,
)
from uuid import UUID, uuid4

import pytest
from pdrd_knowledge_service.application.use_cases.recover_stale_technical_assignments import (
    RecoverStaleTechnicalAssignments,
)
from pdrd_knowledge_service.domain.technical_assignment import (
    PDF_MIME_TYPE,
    TechnicalAssignment,
    TechnicalAssignmentIndexStatus,
)
from pdrd_knowledge_service.domain.technical_assignment_outbox import (
    TechnicalAssignmentOutboxMessage,
)

_SYNTHETIC_T_COUNT = 250
_NOW = datetime(
    2026,
    9,
    11,
    6,
    0,
    tzinfo=UTC,
)


class LoadAssignmentRepository:
    """Synthetic in-memory T repository."""

    def __init__(
        self,
        state: dict[UUID, TechnicalAssignment],
    ) -> None:
        """Сохраняет state."""
        self.state = state

    async def get_recoverable(
        self,
        *,
        stale_indexing_before: object,
        deadline_before: object,
        limit: int,
    ) -> list[TechnicalAssignment]:
        """Возвращает bounded batch."""
        del stale_indexing_before, deadline_before
        return list(self.state.values())[:limit]

    async def update(
        self,
        assignment: TechnicalAssignment,
    ) -> None:
        """Сохраняет assignment."""
        self.state[assignment.technical_assignment_id] = assignment


class LoadOutbox:
    """Synthetic T outbox."""

    def __init__(self) -> None:
        """Создаёт event list."""
        self.messages: list[TechnicalAssignmentOutboxMessage] = []

    async def add(
        self,
        message: TechnicalAssignmentOutboxMessage,
    ) -> None:
        """Сохраняет event."""
        self.messages.append(
            message,
        )


class LoadUow:
    """Synthetic T UoW."""

    def __init__(
        self,
        state: dict[UUID, TechnicalAssignment],
        outbox: LoadOutbox,
    ) -> None:
        """Создаёт repositories."""
        self.assignments = LoadAssignmentRepository(
            state,
        )
        self.outbox = outbox
        self.sections = object()

    async def __aenter__(self) -> "LoadUow":
        """Открывает UoW."""
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        """Закрывает UoW."""

    async def commit(self) -> None:
        """Имитирует commit."""

    async def rollback(self) -> None:
        """Имитирует rollback."""


class LoadFactory:
    """Factory synthetic UoW."""

    def __init__(
        self,
        state: dict[UUID, TechnicalAssignment],
        outbox: LoadOutbox,
    ) -> None:
        """Сохраняет state."""
        self.state = state
        self.outbox = outbox

    def __call__(self) -> LoadUow:
        """Возвращает UoW."""
        return LoadUow(
            self.state,
            self.outbox,
        )


@pytest.mark.asyncio
async def test_recovery_handles_250_synthetic_stale_t_jobs() -> None:
    """250 stale T jobs losslessly получают 250 новых outbox events."""
    state: dict[UUID, TechnicalAssignment] = {}

    for _ in range(
        _SYNTHETIC_T_COUNT,
    ):
        assignment = TechnicalAssignment(
            technical_assignment_id=uuid4(),
            analysis_document_id=uuid4(),
            section_id=uuid4(),
            original_name="ТЗ.pdf",
            mime_type=PDF_MIME_TYPE,
            size_bytes=100,
            sha256="b" * 64,
            index_status=TechnicalAssignmentIndexStatus.INDEXING,
            index_error=None,
            indexed_at=None,
            created_at=_NOW
            - timedelta(
                minutes=5,
            ),
            updated_at=_NOW
            - timedelta(
                minutes=3,
            ),
        )
        state[assignment.technical_assignment_id] = assignment

    outbox = LoadOutbox()

    recovery = RecoverStaleTechnicalAssignments(
        unit_of_work_factory=LoadFactory(
            state,
            outbox,
        ),  # type: ignore[arg-type]
        max_runtime_seconds=1740,
        stale_indexing_seconds=120,
        clock=lambda: _NOW,
    )

    report = await recovery.execute(
        limit=_SYNTHETIC_T_COUNT,
    )

    assert report.selected == _SYNTHETIC_T_COUNT
    assert report.requeued == _SYNTHETIC_T_COUNT
    assert report.failed == 0
    assert len(outbox.messages) == _SYNTHETIC_T_COUNT
    assert (
        len({message.message_id for message in outbox.messages}) == _SYNTHETIC_T_COUNT
    )
    assert all(
        assignment.index_status is TechnicalAssignmentIndexStatus.QUEUED
        for assignment in state.values()
    )
