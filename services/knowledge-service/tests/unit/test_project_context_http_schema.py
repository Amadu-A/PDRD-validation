# services/knowledge-service/tests/unit/test_project_context_http_schema.py

"""Unit tests HTTP contract reusable Project Context cache."""

from uuid import uuid4

from pdrd_knowledge_service.domain.project_context import (
    ProjectContextValidationItem,
    ProjectContextValidationSnapshot,
)
from pdrd_knowledge_service.transport.http.schemas.project_context import (
    CreateProjectContextRequest,
    ProjectContextValidationSnapshotPayload,
    ResolveProjectContextCacheRequest,
)


def validation() -> ProjectContextValidationSnapshot:
    """Возвращает deterministic validation."""
    item = ProjectContextValidationItem(
        page_number=5,
        kind="explanatory_note",
        confidence=0.99,
        reason="Пояснительная записка.",
    )

    return ProjectContextValidationSnapshot(
        enabled=True,
        pages_count=1,
        classifications=(item,),
        warnings=(),
    )


def test_validation_snapshot_round_trip() -> None:
    """Validation можно передать Knowledge API без потери данных."""
    source = validation()

    payload = ProjectContextValidationSnapshotPayload.from_domain(
        source,
    )

    restored = payload.to_domain()

    assert restored == source


def test_resolve_cache_request_accepts_pages() -> None:
    """Resolver получает исходные страницы для content hash."""
    context_id = uuid4()

    request = ResolveProjectContextCacheRequest.model_validate(
        {
            "context_id": str(
                context_id,
            ),
            "enabled": True,
            "pages": [
                {
                    "page_number": 5,
                    "text": ("Пояснительная записка."),
                }
            ],
        }
    )

    assert request.context_id == (context_id)

    assert request.enabled is True

    assert (
        len(
            request.pages,
        )
        == 1
    )


def test_create_request_accepts_cache_identity_and_validation() -> None:
    """Create получает cache key и verified VLM snapshot."""
    context_id = uuid4()

    payload = ProjectContextValidationSnapshotPayload.from_domain(
        validation(),
    )

    request = CreateProjectContextRequest(
        context_id=context_id,
        enabled=True,
        cache_key="abc123",
        validation=payload,
        pages=[],
    )

    assert request.cache_key == "abc123"

    assert request.validation is not None

    assert request.validation.to_domain() == validation()
