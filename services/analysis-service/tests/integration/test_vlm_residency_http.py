# services/analysis-service/tests/integration/test_vlm_residency_http.py

"""HTTP integration tests bounded VLM residency."""

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


class ResidentRecordingVisionModel:
    """Fake VLM с bounded residency capability."""

    def __init__(
        self,
    ) -> None:
        """Инициализирует counters."""
        self.scope_depth = 0

        self.scope_enters = 0
        self.scope_exits = 0

        self.calls = 0

        self.call_depths: list[int] = []

        self.batch_sizes: list[int] = []

    @asynccontextmanager
    async def residency_scope(
        self,
    ) -> AsyncIterator[None]:
        """Записывает request-scoped residency."""
        self.scope_enters += 1

        self.scope_depth += 1

        try:
            yield

        finally:
            self.scope_depth -= 1

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
        """Возвращает findings и проверяет active scope."""
        assert prompt
        assert schema
        assert num_predict > 0
        assert seed > 0
        assert stage.startswith(
            "finalization:",
        )
        assert image_bytes is None

        finding_ids = tuple(
            str(
                finding_id,
            )
            for finding_id in (
                schema["properties"]["findings"]["items"]["properties"]["finding_id"][
                    "enum"
                ]
            )
        )

        self.calls += 1

        self.call_depths.append(
            self.scope_depth,
        )

        self.batch_sizes.append(
            len(
                finding_ids,
            )
        )

        return GenerationResult(
            payload={
                "summary": "done",
                "findings": [
                    {
                        "finding_id": finding_id,
                        "comment": (f"Финализировано {finding_id}."),
                        "recommendation": (f"Исправить {finding_id}."),
                        "experience_source_ids": [],
                        "normative_source_ids": [],
                    }
                    for finding_id in finding_ids
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


def _finding_payload(
    index: int,
) -> dict[str, Any]:
    """Создаёт HTTP finding payload."""
    return {
        "finding_id": f"p1-f{index}",
        "page": 1,
        "page_type": "scheme",
        "category": "scheme_logic",
        "severity": "warning",
        "status": "needs_review",
        "comment": f"Исходное замечание {index}.",
        "evidence": f"Конкретный факт {index}.",
        "recommendation_draft": (f"Проверить решение {index}."),
        "confidence": 0.8,
        "normative_source_ids": [],
        "basis": "",
        "basis_sources": [],
        "experience_query": f"Опыт {index}.",
        "technical_assignment_source_ids": [],
        "technical_assignment_basis_sources": [],
        "user_package_source_ids": [],
        "user_package_basis_sources": [],
    }


def _build_app(
    model: ResidentRecordingVisionModel,
):
    """Создаёт test app с двумя finalization batches."""
    settings = Settings(
        _env_file=None,
        service_name="PDRD Analysis Service Test",
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


async def test_finalize_multiple_batches_share_one_http_residency_scope() -> None:
    """11 findings используют один residency scope для 10+1 VLM calls."""
    model = ResidentRecordingVisionModel()

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
            "/internal/v1/findings/finalize",
            json={
                "findings": [
                    _finding_payload(
                        index,
                    )
                    for index in range(
                        1,
                        12,
                    )
                ],
                "experience_by_finding": {},
                "normative_candidates": [],
                "normative_candidates_by_finding": {},
            },
        )

    assert response.status_code == 200

    payload = response.json()

    assert (
        len(
            payload["findings"],
        )
        == 11
    )

    assert model.calls == 2

    assert model.batch_sizes == [
        10,
        1,
    ]

    assert model.call_depths == [
        1,
        1,
    ]

    assert model.scope_enters == 1
    assert model.scope_exits == 1

    assert model.scope_depth == 0

    assert payload["metrics"]["vlm_call_count"] == 2


async def test_non_vlm_health_endpoint_does_not_take_residency_scope() -> None:
    """Обычный endpoint не должен занимать GPU lease."""
    model = ResidentRecordingVisionModel()

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
        response = await client.get(
            "/health/live",
        )

    assert response.status_code == 200

    assert model.scope_enters == 0
    assert model.scope_exits == 0

    assert model.calls == 0
