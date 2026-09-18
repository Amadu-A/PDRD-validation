# services/analysis-service/tests/unit/test_project_context_http_schema.py

"""Unit tests HTTP contract Project Context."""

from pdrd_analysis_service.domain.project_context import (
    ProjectContextPageKind,
)
from pdrd_analysis_service.transport.http.project_context_schemas import (
    ProjectContextSourcePayload,
    ValidateProjectContextRequest,
)


def test_project_context_source_accepts_qdrant_point_id() -> None:
    """Принимает tracing metadata от Knowledge Service."""
    payload = ProjectContextSourcePayload(
        source_id="PZ1",
        point_id=("d97d1f68-f5c5-5e86-963c-5560c329f3d1"),
        score=0.87,
        page=2,
        chunk_index=1,
        text=("Описание проектного решения."),
    )

    domain = payload.to_domain()

    assert payload.point_id is not None

    assert domain.source_id == "PZ1"

    assert domain.score == 0.87

    assert domain.page == 2

    assert domain.chunk_index == 1

    assert domain.text == "Описание проектного решения."


def test_validate_request_accepts_cached_validation() -> None:
    """Warm cache validation преобразуется в Domain."""
    request = ValidateProjectContextRequest.model_validate(
        {
            "enabled": True,
            "pages": [
                {
                    "page_number": 5,
                    "text": ("Пояснительная записка."),
                }
            ],
            "cached_validation": {
                "enabled": True,
                "pages_count": 1,
                "classifications": [
                    {
                        "page_number": 5,
                        "kind": ("explanatory_note"),
                        "confidence": 0.99,
                        "reason": ("Cached."),
                    }
                ],
                "warnings": [],
            },
        }
    )

    assert request.cached_validation is not None

    domain = request.cached_validation.to_domain()

    assert domain.enabled is True

    assert domain.pages_count == 1

    assert domain.classifications[0].page_number == 5

    assert domain.classifications[0].kind is ProjectContextPageKind.EXPLANATORY_NOTE
