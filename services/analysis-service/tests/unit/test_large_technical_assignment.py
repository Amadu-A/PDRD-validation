# services/analysis-service/tests/unit/test_large_technical_assignment.py

"""Регрессия полного ТЗ из 1094 требований без увеличения VLM-порции."""

from pdrd_analysis_service.application.use_cases.technical_assignment_validation import (
    CheckPageAgainstTechnicalAssignment,
)
from pdrd_analysis_service.core.settings import PipelineSettings
from pdrd_analysis_service.transport.http.technical_assignment_routes import (
    _validate_requirement_count,
)

from .test_technical_assignment_validation import _facts, _FakeVisionModel, _requirement


async def test_all_1094_requirements_keep_order_and_use_48_item_batches():
    """Все ID доходят до результата, включая остаток последней порции."""
    settings = PipelineSettings()
    requirements = tuple(_requirement(i) for i in range(1, 1095))
    _validate_requirement_count(
        actual=len(requirements),
        max_allowed=settings.technical_assignment_max_requirements_per_page,
    )
    batches = [requirements[i : i + 48] for i in range(0, len(requirements), 48)]
    model = _FakeVisionModel(
        [
            {
                "decisions": {
                    item.requirement_id: {"status": "satisfied", "confidence": 0.8}
                    for item in batch
                },
                "issues": [],
            }
            for batch in batches
        ]
    )
    _, decisions, findings, metrics = await CheckPageAgainstTechnicalAssignment(
        model, 4000, 48, 1800
    ).execute(
        page_number=20,
        page_type="scheme",
        extracted_text="Тестовый лист",
        page_facts=_facts(),
        image_bytes=b"image",
        technical_assignment_id="11111111-1111-4111-8111-111111111111",
        analysis_document_id="22222222-2222-4222-8222-222222222222",
        section_id="33333333-3333-4333-8333-333333333333",
        source_file="ТЗ.pdf",
        source_sha256="a" * 64,
        requirements=requirements,
    )
    assert [item.requirement_id for item in decisions] == [
        item.requirement_id for item in requirements
    ]
    assert len(model.calls) == len(metrics) == 23
    assert len(batches[-1]) == 38 and not findings
    for call, batch in zip(model.calls, batches, strict=True):
        ids = call["schema"]["properties"]["decisions"]["required"]
        assert ids == [item.requirement_id for item in batch]
        assert len(ids) <= 48 and call["num_predict"] == 4000
