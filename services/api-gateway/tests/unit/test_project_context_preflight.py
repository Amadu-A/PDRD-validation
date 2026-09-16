# services/api-gateway/tests/unit/test_project_context_preflight.py

"""Unit tests HTTP Project Context preflight coordinator."""

import json
from uuid import UUID

import httpx
import pytest
from pdrd_api_gateway.application.ports.project_context_preflight import (
    InvalidProjectContextPreflightError,
)
from pdrd_api_gateway.core.settings import (
    AnalysisServiceSettings,
    DocumentServiceSettings,
    KnowledgeServiceSettings,
    ProjectContextPreflightSettings,
)
from pdrd_api_gateway.infrastructure.project_context_preflight import (
    HttpProjectContextPreflightCoordinator,
)

_CONTEXT_ID = UUID(
    "11111111-1111-4111-8111-111111111111",
)


def _document_response() -> dict[str, object]:
    """Возвращает минимальный Document Service payload."""
    return {
        "file_name": "project.pdf",
        "total_pages": 20,
        "selected_pages": [5],
        "pages": [],
        "explanatory_note_context": {
            "enabled": True,
            "start_page": 5,
            "end_page": 6,
            "pages_count": 2,
            "pages": [
                {
                    "page_number": 5,
                    "text": "Пояснительная записка. " * 10,
                },
                {
                    "page_number": 6,
                    "text": "Таблица оборудования. " * 10,
                },
            ],
        },
    }


def _validation_response() -> dict[str, object]:
    """Возвращает semantic warning без fatal validation error."""
    return {
        "enabled": True,
        "pages_count": 2,
        "classifications": [
            {
                "page_number": 5,
                "kind": "explanatory_note",
                "confidence": 0.98,
                "reason": "Описание проектных решений.",
            },
            {
                "page_number": 6,
                "kind": "specification",
                "confidence": 0.91,
                "reason": "Страница содержит большую таблицу оборудования.",
            },
        ],
        "requires_confirmation": True,
        "warnings": [
            {
                "page_number": 6,
                "kind": "specification",
                "confidence": 0.91,
                "reason": "Страница содержит большую таблицу оборудования.",
            },
        ],
        "metrics": [],
    }


def _coordinator(
    transport: httpx.AsyncBaseTransport,
) -> HttpProjectContextPreflightCoordinator:
    """Создаёт coordinator с test endpoints."""
    return HttpProjectContextPreflightCoordinator(
        document_service=DocumentServiceSettings(
            base_url="http://document-service",
        ),
        analysis_service=AnalysisServiceSettings(
            base_url="http://analysis-service",
        ),
        knowledge_service=KnowledgeServiceSettings(
            base_url="http://knowledge-service",
        ),
        settings=ProjectContextPreflightSettings(
            request_timeout_seconds=10.0,
            connect_timeout_seconds=1.0,
        ),
        transport=transport,
    )


@pytest.mark.asyncio
async def test_cold_preflight_builds_cache_and_returns_warning() -> None:
    """MISS валидирует ПЗ, строит cache и возвращает confirmation warning."""
    calls: list[str] = []

    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        calls.append(
            request.url.path,
        )

        if request.url.path == "/internal/v1/pdf/extract":
            return httpx.Response(
                200,
                json=_document_response(),
            )

        if request.url.path == "/internal/v1/project-contexts/resolve-cache":
            return httpx.Response(
                200,
                json={
                    "context_id": str(
                        _CONTEXT_ID,
                    ),
                    "enabled": True,
                    "cache_key": "cache-key",
                    "cache_hit": False,
                    "collection_name": "cache-alias",
                    "pages_count": 2,
                    "chunks_count": 0,
                    "vector_size": 0,
                    "validation": None,
                },
            )

        if request.url.path == "/internal/v1/project-context/validate":
            body = json.loads(
                request.content,
            )

            assert body["cached_validation"] is None

            return httpx.Response(
                200,
                json=_validation_response(),
            )

        if request.url.path == "/internal/v1/project-contexts":
            body = json.loads(
                request.content,
            )

            assert body["cache_key"] == "cache-key"

            assert body["validation"]["warnings"][0]["page_number"] == 6

            return httpx.Response(
                200,
                json={
                    "context_id": str(
                        _CONTEXT_ID,
                    ),
                    "enabled": True,
                    "collection_name": "cache-alias",
                    "pages_count": 2,
                    "chunks_count": 2,
                    "vector_size": 4096,
                    "cache_key": "cache-key",
                    "cache_hit": False,
                    "validation": (body["validation"]),
                },
            )

        raise AssertionError(
            f"Unexpected path: {request.url.path}",
        )

    coordinator = _coordinator(
        httpx.MockTransport(
            handler,
        )
    )

    result = await coordinator.execute(
        pdf_content=b"%PDF-test",
        file_name="project.pdf",
        start_page=5,
        end_page=6,
    )

    assert result.cache_hit is False

    assert result.cache_built is True

    assert result.requires_confirmation is True

    assert result.pages_count == 2

    assert (
        len(
            result.warnings,
        )
        == 1
    )

    assert result.warnings[0].page_number == 6

    assert calls == [
        "/internal/v1/pdf/extract",
        "/internal/v1/project-contexts/resolve-cache",
        "/internal/v1/project-context/validate",
        "/internal/v1/project-contexts",
    ]


@pytest.mark.asyncio
async def test_warm_preflight_reuses_cached_validation_without_rebuild() -> None:
    """HIT передаёт cached validation в Analysis Service и не строит cache."""
    calls: list[str] = []

    cached_validation = {
        "enabled": True,
        "pages_count": 2,
        "classifications": (_validation_response()["classifications"]),
        "warnings": (_validation_response()["warnings"]),
    }

    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        calls.append(
            request.url.path,
        )

        if request.url.path == "/internal/v1/pdf/extract":
            return httpx.Response(
                200,
                json=_document_response(),
            )

        if request.url.path == "/internal/v1/project-contexts/resolve-cache":
            return httpx.Response(
                200,
                json={
                    "context_id": str(
                        _CONTEXT_ID,
                    ),
                    "enabled": True,
                    "cache_key": "cache-key",
                    "cache_hit": True,
                    "collection_name": "cache-alias",
                    "pages_count": 2,
                    "chunks_count": 2,
                    "vector_size": 4096,
                    "validation": cached_validation,
                },
            )

        if request.url.path == "/internal/v1/project-context/validate":
            body = json.loads(
                request.content,
            )

            assert body["cached_validation"] == cached_validation

            return httpx.Response(
                200,
                json=_validation_response(),
            )

        raise AssertionError(
            f"Unexpected path: {request.url.path}",
        )

    coordinator = _coordinator(
        httpx.MockTransport(
            handler,
        )
    )

    result = await coordinator.execute(
        pdf_content=b"%PDF-test",
        file_name="project.pdf",
        start_page=5,
        end_page=6,
    )

    assert result.cache_hit is True

    assert result.cache_built is False

    assert result.requires_confirmation is True

    assert calls == [
        "/internal/v1/pdf/extract",
        "/internal/v1/project-contexts/resolve-cache",
        "/internal/v1/project-context/validate",
    ]


@pytest.mark.asyncio
async def test_preflight_preserves_structural_validation_error() -> None:
    """Deterministic validation остаётся blocking user error."""

    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        if request.url.path == "/internal/v1/pdf/extract":
            return httpx.Response(
                200,
                json=_document_response(),
            )

        if request.url.path == "/internal/v1/project-contexts/resolve-cache":
            return httpx.Response(
                200,
                json={
                    "context_id": str(
                        _CONTEXT_ID,
                    ),
                    "enabled": True,
                    "cache_key": "cache-key",
                    "cache_hit": False,
                    "collection_name": "cache-alias",
                    "pages_count": 2,
                    "chunks_count": 0,
                    "vector_size": 0,
                    "validation": None,
                },
            )

        if request.url.path == "/internal/v1/project-context/validate":
            return httpx.Response(
                422,
                json={
                    "detail": ("На страницах ПЗ недостаточно извлекаемого текста: 6."),
                },
            )

        raise AssertionError(
            f"Unexpected path: {request.url.path}",
        )

    coordinator = _coordinator(
        httpx.MockTransport(
            handler,
        )
    )

    with pytest.raises(
        InvalidProjectContextPreflightError,
        match="недостаточно извлекаемого текста",
    ):
        await coordinator.execute(
            pdf_content=b"%PDF-test",
            file_name="project.pdf",
            start_page=5,
            end_page=6,
        )
