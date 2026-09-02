# services/analysis-service/tests/integration/test_http_api.py

"""HTTP integration tests Analysis Service."""

import base64
from typing import Any

import httpx
from pdrd_analysis_service.application.use_cases import (
    BuildNormativeQueries,
    CheckPageAgainstNorms,
    CheckReadiness,
    FinalizeFindings,
    UnderstandPage,
)
from pdrd_analysis_service.core.container import (
    ApplicationContainer,
)
from pdrd_analysis_service.core.settings import (
    Settings,
)
from pdrd_analysis_service.domain.analysis import (
    GenerationMetrics,
    GenerationResult,
)
from pdrd_analysis_service.main import (
    create_app,
)


class FakeVisionModel:
    """Fake VLM HTTP tests."""

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
        """Возвращает payload по stage."""
        assert prompt
        assert schema
        assert num_predict
        assert seed

        if stage.startswith(
            "page_understanding:",
        ):
            payload = {
                "discipline": "ЭОМ",
                "page_type": "scheme",
                "summary": "Тестовая схема",
                "objects": [
                    "ЩР-1",
                ],
                "connections": [],
                "labels": [
                    "PE",
                ],
                "normative_queries": [
                    "заземление оборудования",
                ],
            }

        elif stage.startswith(
            "normative_check:",
        ):
            payload = {
                "summary": "Найдено нарушение.",
                "violations": [
                    {
                        "category": "normative_control",
                        "severity": "error",
                        "status": "confirmed",
                        "comment": ("Корпус не заземлён."),
                        "evidence": ("PE отсутствует."),
                        "recommendation_draft": ("Добавить PE."),
                        "confidence": 0.9,
                        "normative_source_ids": [
                            "N1",
                        ],
                    }
                ],
            }

        else:
            payload = {
                "summary": "Готово.",
                "findings": [
                    {
                        "finding_id": "p1-f1",
                        "comment": ("Отсутствует защитное заземление корпуса."),
                        "recommendation": ("Предусмотреть PE-проводник."),
                        "experience_source_ids": [
                            "E1",
                        ],
                    }
                ],
            }

        return GenerationResult(
            payload=payload,
            metrics=GenerationMetrics(
                attempt=1,
                done_reason="stop",
                requested_num_predict=100,
                total_duration_ms=1.0,
                load_duration_ms=0.0,
                prompt_eval_count=1,
                eval_count=1,
                content_length=10,
                thinking_length=0,
            ),
        )

    async def is_ready(self) -> bool:
        """Возвращает readiness."""
        return True


def build_app():
    """Создаёт test application."""
    settings = Settings(
        _env_file=None,
        service_name=("PDRD Analysis Service Test"),
        service_version="0.1.0-test",
        environment="test",
    )

    vision_model = FakeVisionModel()

    container = ApplicationContainer(
        settings=settings,
        understand_page=UnderstandPage(
            vision_model=vision_model,
            num_predict=1600,
        ),
        build_normative_queries=(
            BuildNormativeQueries(
                max_queries=7,
            )
        ),
        check_page_against_norms=(
            CheckPageAgainstNorms(
                vision_model=vision_model,
                num_predict=2600,
                max_issues=10,
                normative_text_limit=700,
            )
        ),
        finalize_findings=FinalizeFindings(
            vision_model=vision_model,
            num_predict=1800,
            batch_size=2,
            experience_context_limit=600,
            experience_min_score=0.55,
        ),
        check_readiness=CheckReadiness(
            vision_model=vision_model,
        ),
    )

    return create_app(
        container=container,
    )


async def request(
    method: str,
    path: str,
    *,
    json: dict[str, Any] | None = None,
) -> httpx.Response:
    """Выполняет HTTP request через ASGI transport."""
    app = build_app()

    transport = httpx.ASGITransport(
        app=app,
    )

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.request(
            method,
            path,
            json=json,
        )


def encoded_image() -> str:
    """Возвращает fake PNG bytes как Base64."""
    return base64.b64encode(
        b"fake-png",
    ).decode(
        "ascii",
    )


def facts_payload() -> dict[str, Any]:
    """Возвращает PageFacts HTTP payload."""
    return {
        "discipline": "ЭОМ",
        "page_type": "scheme",
        "summary": "Схема",
        "objects": [
            "ЩР-1",
        ],
        "connections": [],
        "labels": [
            "PE",
        ],
        "normative_queries": [
            "заземление оборудования",
        ],
    }


def normative_payload() -> dict[str, Any]:
    """Возвращает normative source payload."""
    return {
        "source_id": "N1",
        "point_id": "point-1",
        "score": 0.8,
        "source_file": "PUE.pdf",
        "source_path": "/PUE.pdf",
        "page": 51,
        "chunk_index": 3,
        "text": "Корпуса подлежат заземлению.",
    }


async def test_health_ready() -> None:
    """Проверяет health endpoints."""
    live = await request(
        "GET",
        "/health/live",
    )

    ready = await request(
        "GET",
        "/health/ready",
    )

    assert live.status_code == 200
    assert ready.status_code == 200

    assert ready.json()["dependencies"]["vision_model"] is True


async def test_understand_endpoint() -> None:
    """Проверяет page understanding API."""
    response = await request(
        "POST",
        "/internal/v1/pages/understand",
        json={
            "page_number": 1,
            "heuristic_page_type": "unknown",
            "extracted_text": "test",
            "image_base64": encoded_image(),
        },
    )

    assert response.status_code == 200

    payload = response.json()

    assert payload["facts"]["discipline"] == "ЭОМ"


async def test_normative_queries_endpoint() -> None:
    """Проверяет query builder API."""
    response = await request(
        "POST",
        "/internal/v1/pages/normative-queries",
        json={
            "page_facts": facts_payload(),
            "extracted_text": "test",
            "project_context_texts": [],
        },
    )

    assert response.status_code == 200

    assert len(response.json()["queries"]) >= 1


async def test_check_norms_endpoint() -> None:
    """Проверяет normative check API."""
    response = await request(
        "POST",
        "/internal/v1/pages/check-norms",
        json={
            "page_number": 1,
            "extracted_text": "test",
            "page_facts": facts_payload(),
            "normative_sources": [
                normative_payload(),
            ],
            "image_base64": encoded_image(),
        },
    )

    assert response.status_code == 200

    finding = response.json()["findings"][0]

    assert finding["finding_id"] == "p1-f1"

    assert finding["normative_source_ids"] == ["N1"]


async def test_finalize_filters_low_score_experience() -> None:
    """Проверяет finalization HTTP API."""
    check_response = await request(
        "POST",
        "/internal/v1/pages/check-norms",
        json={
            "page_number": 1,
            "extracted_text": "test",
            "page_facts": facts_payload(),
            "normative_sources": [
                normative_payload(),
            ],
            "image_base64": encoded_image(),
        },
    )

    finding = check_response.json()["findings"][0]

    response = await request(
        "POST",
        "/internal/v1/findings/finalize",
        json={
            "findings": [
                finding,
            ],
            "experience_by_finding": {
                "p1-f1": [
                    {
                        "source_id": "E1",
                        "point_id": "exp-1",
                        "score": 0.8,
                        "project_id": "project",
                        "issue_id": "issue",
                        "issue_text": "заземление",
                        "status": "fixed",
                        "verified_fixed": True,
                        "before_page": 1,
                        "after_page": 2,
                        "before_context": "без PE",
                        "after_context": "с PE",
                    }
                ]
            },
        },
    )

    assert response.status_code == 200

    result = response.json()["findings"][0]

    assert result["experience_sources"][0]["source_id"] == "E1"


async def test_invalid_base64_is_rejected() -> None:
    """Проверяет защиту от повреждённого изображения."""
    response = await request(
        "POST",
        "/internal/v1/pages/understand",
        json={
            "page_number": 1,
            "heuristic_page_type": "unknown",
            "extracted_text": "test",
            "image_base64": "***broken***",
        },
    )

    assert response.status_code == 422
