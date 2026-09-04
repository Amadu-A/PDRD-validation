# services/api-gateway/tests/unit/test_technical_assignment_snapshot.py

"""Unit tests immutable snapshot технического задания."""

from hashlib import sha256
from uuid import uuid4

import pytest
from pdrd_api_gateway.domain.technical_assignment import (
    DOC_MIME_TYPE,
    DOCX_MIME_TYPE,
    PDF_MIME_TYPE,
    InvalidTechnicalAssignmentSnapshotError,
    TechnicalAssignmentSnapshot,
)


@pytest.mark.parametrize(
    (
        "file_name",
        "mime_type",
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
def test_create_snapshot_supports_expected_formats(
    file_name: str,
    mime_type: str,
) -> None:
    """PDF/DOC/DOCX создают deterministic snapshot."""
    content = b"technical-assignment"

    snapshot = TechnicalAssignmentSnapshot.create(
        analysis_document_id=uuid4(),
        section_id=uuid4(),
        source_file=file_name,
        content=content,
    )

    assert snapshot.mime_type == mime_type

    assert snapshot.size_bytes == len(
        content,
    )

    assert (
        snapshot.sha256
        == sha256(
            content,
        ).hexdigest()
    )


def test_snapshot_roundtrip_preserves_identity() -> None:
    """JSONB serialization не меняет immutable metadata."""
    snapshot = TechnicalAssignmentSnapshot.create(
        analysis_document_id=uuid4(),
        section_id=uuid4(),
        source_file="ТЗ объекта.pdf",
        content=b"content",
    )

    restored = TechnicalAssignmentSnapshot.from_payload(
        snapshot.as_payload(),
    )

    assert restored == snapshot


def test_snapshot_rejects_unsupported_extension() -> None:
    """Произвольный файл не становится ТЗ."""
    with pytest.raises(
        InvalidTechnicalAssignmentSnapshotError,
        match="PDF, DOC или DOCX",
    ):
        TechnicalAssignmentSnapshot.create(
            analysis_document_id=uuid4(),
            section_id=uuid4(),
            source_file="requirements.txt",
            content=b"text",
        )


def test_snapshot_rejects_path_traversal() -> None:
    """Filename не используется как произвольный filesystem path."""
    with pytest.raises(
        InvalidTechnicalAssignmentSnapshotError,
        match="недопустимый путь",
    ):
        TechnicalAssignmentSnapshot.create(
            analysis_document_id=uuid4(),
            section_id=uuid4(),
            source_file="../ТЗ.pdf",
            content=b"content",
        )
