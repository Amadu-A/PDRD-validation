# services/analysis-service/tests/unit/test_technical_assignment_http_limits.py

"""Unit tests bounded transport T-first validation."""

from uuid import UUID

import pytest
from fastapi import HTTPException, status
from pdrd_analysis_service.core.settings import PipelineSettings
from pdrd_analysis_service.transport.http.technical_assignment_routes import (
    _validate_requirement_count,
)
from pdrd_analysis_service.transport.http.technical_assignment_schemas import (
    CheckTechnicalAssignmentRequest,
)


def _requirement(
    index: int,
) -> dict[str, object]:
    """Возвращает один валидный atomic T requirement."""
    return {
        "point_id": f"point-{index}",
        "requirement_id": f"T-R{index}",
        "requirement_index": index,
        "page": 1,
        "requirement_strength": "candidate",
        "scopes": [],
        "normative_refs": [],
        "source_text": f"Исходное требование {index}.",
        "text": f"Требование {index}.",
    }


def _request(
    *,
    requirements_count: int,
) -> CheckTechnicalAssignmentRequest:
    """Создаёт transport request заданного размера."""
    return CheckTechnicalAssignmentRequest(
        page_number=1,
        extracted_text="Тестовый лист.",
        page_facts={
            "discipline": "ЭОМ",
            "page_type": "scheme",
            "summary": "Схема",
            "objects": [],
            "connections": [],
            "labels": [],
            "normative_queries": [],
        },
        image_base64="ZmFrZQ==",
        technical_assignment_id=UUID(
            "11111111-1111-4111-8111-111111111111",
        ),
        analysis_document_id=UUID(
            "22222222-2222-4222-8222-222222222222",
        ),
        section_id=UUID(
            "33333333-3333-4333-8333-333333333333",
        ),
        source_file="ТЗ.pdf",
        source_sha256="a" * 64,
        requirements=[
            _requirement(
                index,
            )
            for index in range(
                1,
                requirements_count + 1,
            )
        ],
    )


def test_default_requirement_limit_is_bounded() -> None:
    """Baseline допускает большой, но конечный T-first input."""
    settings = PipelineSettings()

    assert settings.technical_assignment_max_requirements_per_page == 1000


def test_transport_schema_accepts_more_than_old_page_size() -> None:
    """Transport schema не обрезает feed на старой границе 200."""
    request = _request(
        requirements_count=201,
    )

    assert (
        len(
            request.requirements,
        )
        == 201
    )


def test_requirement_limit_allows_configured_boundary() -> None:
    """Configured limit включительно является допустимым."""
    _validate_requirement_count(
        actual=1000,
        max_allowed=1000,
    )


def test_requirement_limit_rejects_oversized_request() -> None:
    """Oversized T-first request завершается до VLM."""
    with pytest.raises(
        HTTPException,
    ) as error_info:
        _validate_requirement_count(
            actual=1001,
            max_allowed=1000,
        )

    error = error_info.value

    assert error.status_code == status.HTTP_413_CONTENT_TOO_LARGE

    detail = str(
        error.detail,
    )

    assert "1001" in detail

    assert "1000" in detail
