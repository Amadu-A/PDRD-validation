# services/api-gateway/tests/unit/test_technical_assignment_artifacts.py

"""Unit tests filesystem storage технического задания."""

from pathlib import Path
from uuid import uuid4

import pytest
from pdrd_api_gateway.application.ports.artifacts import (
    AnalysisArtifactsNotFoundError,
)
from pdrd_api_gateway.domain.analysis_submission import (
    AnalysisSubmission,
)
from pdrd_api_gateway.infrastructure.storage.filesystem import (
    LocalFilesystemAnalysisArtifactStore,
)


@pytest.mark.asyncio
async def test_technical_assignment_storage_roundtrip(
    tmp_path: Path,
) -> None:
    """ТЗ хранится внутри каталога конкретного analysis document."""
    store = LocalFilesystemAnalysisArtifactStore(
        root_path=tmp_path,
    )

    submission = AnalysisSubmission.create(
        pdf_present=True,
        cad_present=False,
        pages="1",
        pdf_file_name="drawing.pdf",
        cad_file_name=None,
    )

    await store.save_request(
        submission=submission,
        pdf_content=b"pdf",
        cad_content=None,
    )

    await store.save_technical_assignment(
        document_id=submission.document_id,
        content=b"technical-assignment",
    )

    restored = await store.load_technical_assignment(
        document_id=(submission.document_id),
    )

    assert restored == b"technical-assignment"

    assert (
        tmp_path
        / str(
            submission.document_id,
        )
        / "technical_assignment.bin"
    ).is_file()


@pytest.mark.asyncio
async def test_technical_assignment_requires_existing_request_directory(
    tmp_path: Path,
) -> None:
    """ТЗ нельзя отвязать от analysis request."""
    store = LocalFilesystemAnalysisArtifactStore(
        root_path=tmp_path,
    )

    with pytest.raises(
        AnalysisArtifactsNotFoundError,
    ):
        await store.save_technical_assignment(
            document_id=uuid4(),
            content=b"technical-assignment",
        )
