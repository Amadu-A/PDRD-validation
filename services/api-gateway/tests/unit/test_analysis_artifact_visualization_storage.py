# services/api-gateway/tests/unit/test_analysis_artifact_visualization_storage.py

"""Unit tests filesystem reusable visualization artifacts."""

import base64
from pathlib import Path

import pytest
from pdrd_api_gateway.application.ports.analysis_visualization import (
    AnalysisBoundingBox,
    AnalysisPagePreview,
    AnalysisTextWord,
)
from pdrd_api_gateway.application.ports.artifacts import (
    AnalysisArtifactStorageError,
)
from pdrd_api_gateway.domain.analysis_submission import (
    AnalysisSubmission,
)
from pdrd_api_gateway.infrastructure.storage.filesystem import (
    LocalFilesystemAnalysisArtifactStore,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\nsynthetic-stage7-png"


def preview_page(
    page_number: int,
) -> AnalysisPagePreview:
    """Создаёт synthetic persisted PDF preview."""
    return AnalysisPagePreview(
        page_number=page_number,
        width_points=841.89,
        height_points=595.28,
        image_base64=base64.b64encode(
            PNG_BYTES,
        ).decode(
            "ascii",
        ),
        extracted_text=(f"Page {page_number} XT1"),
        text_words=(
            AnalysisTextWord(
                text="XT1",
                bbox=AnalysisBoundingBox(
                    x_min=100,
                    y_min=200,
                    x_max=140,
                    y_max=220,
                ),
                block_no=1,
                line_no=1,
                word_no=1,
            ),
        ),
    )


async def saved_store(
    tmp_path: Path,
) -> tuple[
    LocalFilesystemAnalysisArtifactStore,
    AnalysisSubmission,
]:
    """Создаёт artifact store с persisted request."""
    submission = AnalysisSubmission.create(
        pdf_present=True,
        cad_present=False,
        pages="1",
        pdf_file_name=("drawing.pdf"),
        cad_file_name=None,
    )

    store = LocalFilesystemAnalysisArtifactStore(
        root_path=tmp_path,
    )

    await store.save_request(
        submission=submission,
        pdf_content=b"pdf",
        cad_content=None,
    )

    return (
        store,
        submission,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pages_count",
    [
        1,
        50,
    ],
)
async def test_visualization_artifact_roundtrip_keeps_png_out_of_manifest(
    tmp_path: Path,
    pages_count: int,
) -> None:
    """1/50 pages сохраняются без base64 внутри JSON manifest."""
    (
        store,
        submission,
    ) = await saved_store(
        tmp_path,
    )

    pages = tuple(
        preview_page(
            page_number,
        )
        for page_number in range(
            1,
            pages_count + 1,
        )
    )

    await store.save_visualization(
        document_id=(submission.document_id),
        pages=pages,
    )

    loaded = await store.load_visualization(
        document_id=(submission.document_id),
    )

    assert loaded == pages

    directory = (
        tmp_path
        / str(
            submission.document_id,
        )
        / "visualization"
    )

    manifest = (directory / "manifest.json").read_text(
        encoding="utf-8",
    )

    assert "image_base64" not in manifest

    assert (directory / "page-1.png").read_bytes() == PNG_BYTES

    assert (directory / f"page-{pages_count}.png").read_bytes() == PNG_BYTES


@pytest.mark.asyncio
async def test_missing_visualization_artifact_is_legacy_cache_miss(
    tmp_path: Path,
) -> None:
    """Legacy job без visualization artifact возвращает None."""
    (
        store,
        submission,
    ) = await saved_store(
        tmp_path,
    )

    assert (
        await store.load_visualization(
            document_id=(submission.document_id),
        )
        is None
    )


@pytest.mark.asyncio
async def test_corrupt_visualization_manifest_is_explicit_storage_error(
    tmp_path: Path,
) -> None:
    """Повреждённый artifact не маскируется под корректный cache hit."""
    (
        store,
        submission,
    ) = await saved_store(
        tmp_path,
    )

    await store.save_visualization(
        document_id=(submission.document_id),
        pages=(
            preview_page(
                1,
            ),
        ),
    )

    manifest_path = (
        tmp_path
        / str(
            submission.document_id,
        )
        / "visualization"
        / "manifest.json"
    )

    manifest_path.write_text(
        "{broken",
        encoding="utf-8",
    )

    with pytest.raises(
        AnalysisArtifactStorageError,
        match="visualization artifact",
    ):
        await store.load_visualization(
            document_id=(submission.document_id),
        )
