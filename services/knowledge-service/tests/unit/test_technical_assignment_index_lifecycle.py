# services/knowledge-service/tests/unit/test_technical_assignment_index_lifecycle.py

"""Unit tests retry lifecycle ТЗ."""

from datetime import (
    UTC,
    datetime,
    timedelta,
)
from uuid import uuid4

from pdrd_knowledge_service.domain.technical_assignment import (
    PDF_MIME_TYPE,
    TechnicalAssignment,
    TechnicalAssignmentIndexStatus,
)

NOW = datetime(
    2026,
    9,
    5,
    12,
    0,
    tzinfo=UTC,
)


def test_indexing_can_return_to_queued_after_gpu_busy() -> None:
    """Transient GPU failure разрешает safe retry."""
    assignment = TechnicalAssignment(
        technical_assignment_id=uuid4(),
        analysis_document_id=uuid4(),
        section_id=uuid4(),
        original_name="ТЗ.pdf",
        mime_type=PDF_MIME_TYPE,
        size_bytes=100,
        sha256="a" * 64,
        index_status=(TechnicalAssignmentIndexStatus.INDEXING),
        index_error=None,
        indexed_at=None,
        created_at=NOW,
        updated_at=NOW,
    )

    queued = assignment.transition_indexing(
        target_status=(TechnicalAssignmentIndexStatus.QUEUED),
        changed_at=(
            NOW
            + timedelta(
                seconds=1,
            )
        ),
    )

    assert queued.index_status is TechnicalAssignmentIndexStatus.QUEUED

    assert queued.index_error is None

    assert queued.indexed_at is None
