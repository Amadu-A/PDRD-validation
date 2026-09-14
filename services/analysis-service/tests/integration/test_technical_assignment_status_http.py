# services/analysis-service/tests/integration/test_technical_assignment_status_http.py

"""HTTP regression tests T-first single status source."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from pdrd_analysis_service.application.use_cases import (
    BuildNormativeQueries,
    CheckPageAgainstNorms,
    CheckReadiness,
    FinalizeFindings,
    LocalizeFindings,
    UnderstandPage,
)
from pdrd_analysis_service.application.use_cases.technical_assignment_validation import (
    CheckPageAgainstTechnicalAssignment,
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


class _ResidentVisionModel:
    """Fake resident VLM для T-first HTTP regression."""

    def __init__(
        self,
    ) -> None:
        """Инициализирует counters."""
        self.scope_enters = 0
        self.scope_exits = 0
        self.calls = 0

    @asynccontextmanager
    async def residency_scope(
        self,
    ) -> AsyncIterator[None]:
        """Эмулирует bounded VLM residency."""
        self.scope_enters += 1

        try:
            yield

        finally:
            self.scope_exits += 1

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
        """Возвращает schema-conformant T-first response."""
        assert prompt
        assert schema
        assert num_predict > 0
        assert seed > 0
        assert stage == ("technical_assignment_check:19:1")
        assert image_bytes is not None

        self.calls += 1

        return GenerationResult(
            payload={
                "decisions": {
                    "T-R1": {
                        "status": "violated",
                        "confidence": 0.94,
                    },
                },
                "issues": [
                    {
                        "requirement_id": "T-R1",
                        "severity": "error",
                        "comment": ("Требование ТЗ нарушено."),
                        "evidence": ("На принципиальной схеме показано иное решение."),
                        "recommendation_draft": ("Скорректировать проектное решение."),
                    },
                ],
            },
            metrics=GenerationMetrics(
                attempt=1,
                done_reason="stop",
                requested_num_predict=4000,
                total_duration_ms=10.0,
                load_duration_ms=1.0,
                prompt_eval_count=100,
                eval_count=50,
                content_length=300,
                thinking_length=0,
            ),
        )

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает readiness."""
        return True


def _build_app(
    model: _ResidentVisionModel,
):
    """Создаёт Analysis Service test app."""
    settings = Settings(
        _env_file=None,
        service_name=("PDRD Analysis Service Test"),
        service_version="0.1.0-test",
        environment="test",
    )

    container = ApplicationContainer(
        settings=settings,
        understand_page=UnderstandPage(
            vision_model=model,
            num_predict=1600,
        ),
        build_normative_queries=BuildNormativeQueries(
            max_queries=7,
        ),
        check_page_against_norms=CheckPageAgainstNorms(
            vision_model=model,
            num_predict=14000,
            max_issues=50,
            normative_text_limit=700,
        ),
        check_page_against_technical_assignment=(
            CheckPageAgainstTechnicalAssignment(
                vision_model=model,
                num_predict=4000,
                batch_size=48,
                requirement_text_limit=1800,
            )
        ),
        finalize_findings=FinalizeFindings(
            vision_model=model,
            num_predict=4000,
            batch_size=10,
            experience_context_limit=600,
            experience_min_score=0.55,
        ),
        localize_findings=LocalizeFindings(
            vision_model=model,
            num_predict=4000,
        ),
        check_readiness=CheckReadiness(
            vision_model=model,
        ),
    )

    return create_app(
        container=container,
    )


async def test_t_first_http_accepts_issue_without_duplicate_status() -> None:
    """T-first route не превращает корректный model output в 503."""
    model = _ResidentVisionModel()

    app = _build_app(
        model,
    )

    transport = httpx.ASGITransport(
        app=app,
    )

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/internal/v1/pages/check-technical-assignment",
            json={
                "page_number": 19,
                "extracted_text": ("Принципиальная схема котельной."),
                "page_facts": {
                    "discipline": ("Тепломеханические решения"),
                    "page_type": ("Принципиальная схема котельной"),
                    "summary": ("Схема котельной."),
                    "objects": [
                        "Котёл",
                        "Циркуляционный насос",
                    ],
                    "connections": [
                        "Котёл → тепловая сеть",
                    ],
                    "labels": [
                        "PG",
                        "TG",
                    ],
                    "normative_queries": [],
                },
                "image_base64": "aW1hZ2U=",
                "technical_assignment_id": ("11111111-1111-4111-8111-111111111111"),
                "analysis_document_id": ("22222222-2222-4222-8222-222222222222"),
                "section_id": ("33333333-3333-4333-8333-333333333333"),
                "source_file": "ТЗ.docx",
                "source_sha256": "a" * 64,
                "requirements": [
                    {
                        "point_id": "point-1",
                        "requirement_id": "T-R1",
                        "requirement_index": 1,
                        "page": 4,
                        "requirement_strength": ("explicit"),
                        "scopes": [
                            "heating",
                        ],
                        "normative_refs": [],
                        "source_text": ("Исходный текст требования ТЗ."),
                        "text": ("На схеме должен быть предусмотрен элемент."),
                    },
                ],
            },
        )

    assert response.status_code == 200, response.text

    payload = response.json()

    assert (
        len(
            payload["decisions"],
        )
        == 1
    )

    assert payload["decisions"][0]["status"] == "violated"

    assert (
        len(
            payload["findings"],
        )
        == 1
    )

    assert payload["findings"][0]["status"] == "confirmed"

    assert model.calls == 1

    assert model.scope_enters == 1
    assert model.scope_exits == 1
