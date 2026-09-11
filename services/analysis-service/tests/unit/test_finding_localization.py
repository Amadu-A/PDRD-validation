# services/analysis-service/tests/unit/test_finding_localization.py

"""Unit tests visual finding localization."""

from dataclasses import dataclass
from typing import Any

import pytest
from pdrd_analysis_service.application.use_cases.finding_localization import (
    LocalizeFindings,
)
from pdrd_analysis_service.domain.analysis import (
    GenerationMetrics,
    GenerationResult,
)
from pdrd_analysis_service.domain.visualization import (
    FindingLocalizationTarget,
)


@dataclass
class FakeVisionModel:
    """Structured VLM double."""

    payload: dict[
        str,
        Any,
    ]

    calls: int = 0

    async def generate_json(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        num_predict: int,
        seed: int,
        stage: str,
        image_bytes: bytes | None = None,
    ) -> GenerationResult:
        """Возвращает deterministic payload."""
        del prompt, schema, num_predict, seed, stage

        assert image_bytes

        self.calls += 1

        return GenerationResult(
            payload=self.payload,
            metrics=GenerationMetrics(
                attempt=1,
                done_reason="stop",
                requested_num_predict=1200,
                total_duration_ms=10.0,
                load_duration_ms=1.0,
                prompt_eval_count=100,
                eval_count=50,
                content_length=100,
                thinking_length=0,
            ),
        )

    async def is_ready(
        self,
    ) -> bool:
        """Fake readiness."""
        return True


def target(
    finding_id: str,
) -> FindingLocalizationTarget:
    """Создаёт finding target."""
    return FindingLocalizationTarget(
        finding_id=finding_id,
        comment=f"Замечание {finding_id}",
        evidence="Видимый факт.",
    )


@pytest.mark.asyncio
async def test_no_findings_skips_vlm() -> None:
    """Пустой page result не тратит GPU на localization."""
    model = FakeVisionModel(
        payload={},
    )

    use_case = LocalizeFindings(
        vision_model=model,
        num_predict=1200,
    )

    locations, metrics = await use_case.execute(
        page_number=14,
        extracted_text="",
        image_bytes=b"png",
        findings=(),
    )

    assert locations == ()
    assert metrics["done_reason"] == "no_findings"
    assert model.calls == 0


@pytest.mark.asyncio
async def test_localization_maps_exact_ids_and_bbox() -> None:
    """Valid bbox сохраняется в normalized coordinate space."""
    model = FakeVisionModel(
        payload={
            "locations": [
                {
                    "finding_id": "F-2",
                    "status": "located",
                    "bbox": {
                        "x_min": 500,
                        "y_min": 200,
                        "x_max": 800,
                        "y_max": 450,
                    },
                    "confidence": 0.91,
                },
                {
                    "finding_id": "F-1",
                    "status": "located",
                    "bbox": {
                        "x_min": 100,
                        "y_min": 120,
                        "x_max": 250,
                        "y_max": 300,
                    },
                    "confidence": 0.8,
                },
            ],
        },
    )

    use_case = LocalizeFindings(
        vision_model=model,
        num_predict=1200,
    )

    locations, _ = await use_case.execute(
        page_number=14,
        extracted_text="sheet",
        image_bytes=b"png",
        findings=(
            target(
                "F-1",
            ),
            target(
                "F-2",
            ),
        ),
    )

    assert [location.finding_id for location in locations] == [
        "F-1",
        "F-2",
    ]

    assert locations[0].bbox is not None
    assert locations[0].bbox.x_min == 100
    assert locations[1].confidence == 0.91


@pytest.mark.asyncio
async def test_invalid_or_missing_bbox_becomes_unlocated() -> None:
    """Неуверенная geometry не превращается в выдуманный bbox."""
    model = FakeVisionModel(
        payload={
            "locations": [
                {
                    "finding_id": "F-1",
                    "status": "located",
                    "bbox": {
                        "x_min": 900,
                        "y_min": 100,
                        "x_max": 200,
                        "y_max": 300,
                    },
                    "confidence": 0.9,
                },
            ],
        },
    )

    use_case = LocalizeFindings(
        vision_model=model,
        num_predict=1200,
    )

    locations, _ = await use_case.execute(
        page_number=14,
        extracted_text="sheet",
        image_bytes=b"png",
        findings=(
            target(
                "F-1",
            ),
            target(
                "F-2",
            ),
        ),
    )

    assert all(location.status == "unlocated" for location in locations)

    assert all(location.bbox is None for location in locations)


@pytest.mark.asyncio
async def test_localization_preserves_50_synthetic_findings() -> None:
    """Synthetic load: 50 findings входят и выходят без потерь."""
    targets = tuple(
        target(
            f"F-{index:02d}",
        )
        for index in range(
            50,
        )
    )

    model = FakeVisionModel(
        payload={
            "locations": [
                {
                    "finding_id": item.finding_id,
                    "status": "located",
                    "bbox": {
                        "x_min": index * 5,
                        "y_min": index * 5,
                        "x_max": index * 5 + 20,
                        "y_max": index * 5 + 20,
                    },
                    "confidence": 0.75,
                }
                for index, item in enumerate(
                    targets,
                )
            ],
        },
    )

    use_case = LocalizeFindings(
        vision_model=model,
        num_predict=1200,
    )

    locations, _ = await use_case.execute(
        page_number=14,
        extracted_text="synthetic sheet",
        image_bytes=b"png",
        findings=targets,
    )

    assert (
        len(
            locations,
        )
        == 50
    )

    assert [item.finding_id for item in locations] == [
        item.finding_id for item in targets
    ]

    assert all(item.status == "located" for item in locations)

    assert model.calls == 1
