# services/api-gateway/tests/unit/test_analysis_visualization_cache.py

"""Unit tests filesystem derivative visualization cache."""

from uuid import uuid4

import pytest
from pdrd_api_gateway.application.ports.analysis_visualization import (
    AnalysisBoundingBox,
    AnalysisFindingLocation,
    AnalysisVisualRegion,
)
from pdrd_api_gateway.application.ports.analysis_visualization_cache import (
    AnalysisVisualizationLocationPage,
)
from pdrd_api_gateway.infrastructure.storage.visualization_cache import (
    LocalFilesystemAnalysisVisualizationCache,
)


@pytest.mark.asyncio
async def test_location_and_annotated_pdf_cache_round_trip(
    tmp_path,
) -> None:
    """Derivative cache сохраняет typed locations и PDF bytes."""
    document_id = uuid4()

    (
        tmp_path
        / str(
            document_id,
        )
    ).mkdir()

    cache = LocalFilesystemAnalysisVisualizationCache(
        root_path=tmp_path,
    )

    pages = (
        AnalysisVisualizationLocationPage(
            page_number=3,
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
                            source="pdf_text",
                            confidence=1.0,
                            label="XT1",
                        ),
                    ),
                    confidence=1.0,
                    method="pdf_text",
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

    restored = await cache.load_locations(
        document_id=document_id,
    )

    assert restored is not None

    assert restored[0].page_number == 3

    assert restored[0].locations[0].method == "pdf_text"

    assert restored[0].locations[1].status == "unlocated"

    pdf_content = b"%PDF-1.7\nsynthetic"

    await cache.save_annotated_pdf(
        document_id=document_id,
        content=pdf_content,
    )

    assert (
        await cache.load_annotated_pdf(
            document_id=document_id,
        )
        == pdf_content
    )
