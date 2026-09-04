# services/knowledge-service/tests/unit/test_technical_assignment_domain.py

"""Unit tests domain-модели технического задания."""

from datetime import (
    UTC,
    datetime,
    timedelta,
)
from uuid import uuid4

import pytest
from pdrd_knowledge_service.domain.technical_assignment import (
    DOC_MIME_TYPE,
    DOCX_MIME_TYPE,
    PDF_MIME_TYPE,
    TechnicalAssignment,
    TechnicalAssignmentError,
    TechnicalAssignmentIndexStatus,
    resolve_technical_assignment_mime_type,
)

NOW = datetime(
    2026,
    9,
    4,
    12,
    0,
    tzinfo=UTC,
)


@pytest.mark.parametrize(
    (
        "file_name",
        "expected_mime_type",
    ),
    (
        (
            "ТЗ.pdf",
            PDF_MIME_TYPE,
        ),
        (
            "ТЗ.doc",
            DOC_MIME_TYPE,
        ),
        (
            "ТЗ.docx",
            DOCX_MIME_TYPE,
        ),
    ),
)
def test_supported_technical_assignment_formats(
    file_name: str,
    expected_mime_type: str,
) -> None:
    """PDF/DOC/DOCX имеют deterministic MIME type."""
    assert (
        resolve_technical_assignment_mime_type(
            file_name,
        )
        == expected_mime_type
    )


def test_unsupported_technical_assignment_format_is_rejected() -> None:
    """Не допускает произвольный файл вместо ТЗ."""
    with pytest.raises(
        TechnicalAssignmentError,
        match="PDF, DOC или DOCX",
    ):
        resolve_technical_assignment_mime_type(
            "requirements.txt",
        )


def make_assignment() -> TechnicalAssignment:
    """Создаёт валидное ТЗ в состоянии uploaded."""
    return TechnicalAssignment(
        technical_assignment_id=uuid4(),
        analysis_document_id=uuid4(),
        section_id=uuid4(),
        original_name="ТЗ объекта.pdf",
        mime_type=PDF_MIME_TYPE,
        size_bytes=1024,
        sha256="a" * 64,
        index_status=(TechnicalAssignmentIndexStatus.UPLOADED),
        index_error=None,
        indexed_at=None,
        created_at=NOW,
        updated_at=NOW,
    )


def test_technical_assignment_lifecycle_reaches_ready() -> None:
    """Проверяет uploaded -> queued -> indexing -> ready."""
    uploaded = make_assignment()

    queued = uploaded.transition_indexing(
        target_status=(TechnicalAssignmentIndexStatus.QUEUED),
        changed_at=(
            NOW
            + timedelta(
                seconds=1,
            )
        ),
    )

    indexing = queued.transition_indexing(
        target_status=(TechnicalAssignmentIndexStatus.INDEXING),
        changed_at=(
            NOW
            + timedelta(
                seconds=2,
            )
        ),
    )

    ready_at = NOW + timedelta(
        seconds=3,
    )

    ready = indexing.transition_indexing(
        target_status=(TechnicalAssignmentIndexStatus.READY),
        changed_at=ready_at,
    )

    assert ready.index_status is TechnicalAssignmentIndexStatus.READY

    assert ready.indexed_at == ready_at

    assert ready.index_error is None


def test_failed_technical_assignment_requires_error() -> None:
    """FAILED transition сохраняет диагностическую ошибку."""
    queued = make_assignment().transition_indexing(
        target_status=(TechnicalAssignmentIndexStatus.QUEUED),
        changed_at=(
            NOW
            + timedelta(
                seconds=1,
            )
        ),
    )

    indexing = queued.transition_indexing(
        target_status=(TechnicalAssignmentIndexStatus.INDEXING),
        changed_at=(
            NOW
            + timedelta(
                seconds=2,
            )
        ),
    )

    failed = indexing.transition_indexing(
        target_status=(TechnicalAssignmentIndexStatus.FAILED),
        changed_at=(
            NOW
            + timedelta(
                seconds=3,
            )
        ),
        error="CUDA allocation failed",
    )

    assert failed.index_status is TechnicalAssignmentIndexStatus.FAILED

    assert failed.index_error == "CUDA allocation failed"

    assert failed.indexed_at is None


def test_invalid_indexing_transition_is_rejected() -> None:
    """Нельзя перескочить напрямую uploaded -> ready."""
    with pytest.raises(
        TechnicalAssignmentError,
        match="uploaded -> ready",
    ):
        make_assignment().transition_indexing(
            target_status=(TechnicalAssignmentIndexStatus.READY),
            changed_at=(
                NOW
                + timedelta(
                    seconds=1,
                )
            ),
        )


def test_technical_assignment_rejects_path_in_file_name() -> None:
    """Имя ТЗ не может использоваться для path traversal."""
    with pytest.raises(
        TechnicalAssignmentError,
        match="недопустимый путь",
    ):
        TechnicalAssignment(
            technical_assignment_id=uuid4(),
            analysis_document_id=uuid4(),
            section_id=uuid4(),
            original_name="../ТЗ.pdf",
            mime_type=PDF_MIME_TYPE,
            size_bytes=1024,
            sha256="a" * 64,
            index_status=(TechnicalAssignmentIndexStatus.UPLOADED),
            index_error=None,
            indexed_at=None,
            created_at=NOW,
            updated_at=NOW,
        )


def test_technical_assignment_rejects_invalid_sha256() -> None:
    """Snapshot ТЗ требует полный SHA-256."""
    with pytest.raises(
        TechnicalAssignmentError,
        match="SHA-256",
    ):
        TechnicalAssignment(
            technical_assignment_id=uuid4(),
            analysis_document_id=uuid4(),
            section_id=uuid4(),
            original_name="ТЗ.pdf",
            mime_type=PDF_MIME_TYPE,
            size_bytes=1024,
            sha256="abc",
            index_status=(TechnicalAssignmentIndexStatus.UPLOADED),
            index_error=None,
            indexed_at=None,
            created_at=NOW,
            updated_at=NOW,
        )
