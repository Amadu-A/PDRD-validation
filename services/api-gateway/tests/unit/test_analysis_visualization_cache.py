# services/api-gateway/tests/unit/test_analysis_visualization_cache.py

"""Проверки файлового кэша координат и размеченного PDF."""

import json
from uuid import uuid4

import pytest
from pdrd_api_gateway.application.ports.analysis_visualization import (
    AnalysisBoundingBox,
    AnalysisFindingLocation,
    AnalysisVisualRegion,
)
from pdrd_api_gateway.application.ports.analysis_visualization_cache import (
    AnalysisVisualizationCacheError,
    AnalysisVisualizationLocationPage,
)
from pdrd_api_gateway.infrastructure.storage.visualization_cache import (
    LocalFilesystemAnalysisVisualizationCache,
)


@pytest.mark.asyncio
async def test_location_and_annotated_pdf_cache_round_trip(
    tmp_path,
) -> None:
    """Кэш отвергает старые версии и сохраняет геометрию с SHA-256."""
    document_id = uuid4()

    document_directory = tmp_path / str(
        document_id,
    )

    document_directory.mkdir()

    visualization_directory = document_directory / "visualization"

    visualization_directory.mkdir()

    cache = LocalFilesystemAnalysisVisualizationCache(
        root_path=tmp_path,
    )

    legacy_locations = visualization_directory / "locations.json"

    legacy_locations.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "pages": [],
            },
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        AnalysisVisualizationCacheError,
    ):
        await cache.load_locations(
            document_id=document_id,
        )

    for file_name in (
        "annotated.pdf",
        "annotated-v2.pdf",
        "annotated-v3.pdf",
        "annotated-v4.pdf",
        "annotated-v5.pdf",
    ):
        (document_directory / file_name).write_bytes(
            b"%PDF-1.7\nlegacy",
        )

    assert (
        await cache.load_annotated_pdf(
            document_id=document_id,
        )
        is None
    )

    pages = (
        AnalysisVisualizationLocationPage(
            page_number=3,
            source_signature="a" * 64,
            locations=(
                AnalysisFindingLocation.located(
                    finding_id="F-1",
                    regions=(
                        AnalysisVisualRegion(
                            bbox=(
                                AnalysisBoundingBox(
                                    x_min=100,
                                    y_min=200,
                                    x_max=300,
                                    y_max=400,
                                )
                            ),
                            source="analysis_vlm",
                            confidence=0.95,
                            label="XT1",
                        ),
                    ),
                    confidence=0.95,
                    method="analysis_vlm",
                ),
                AnalysisFindingLocation.unlocated(
                    finding_id="F-2",
                ),
            ),
        ),
    )

    await cache.save_locations(
        document_id=document_id,
        pages=pages,
    )

    saved_payload = json.loads(
        legacy_locations.read_text(
            encoding="utf-8",
        )
    )

    assert saved_payload["schema_version"] == 5
    assert saved_payload["pages"][0]["source_signature"] == "a" * 64

    restored = await cache.load_locations(
        document_id=document_id,
    )

    assert restored is not None

    assert restored[0].page_number == 3
    assert restored[0].source_signature == "a" * 64

    assert restored[0].locations[0].method == "analysis_vlm"

    assert restored[0].locations[1].status == "unlocated"

    pdf_content = b"%PDF-1.7\nsynthetic"

    await cache.save_annotated_pdf(
        document_id=document_id,
        content=pdf_content,
    )

    versioned_pdf_path = document_directory / "annotated-v6.pdf"

    assert versioned_pdf_path.read_bytes() == pdf_content

    assert (
        await cache.load_annotated_pdf(
            document_id=document_id,
        )
        == pdf_content
    )
