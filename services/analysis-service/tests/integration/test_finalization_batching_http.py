# services/analysis-service/tests/integration/test_finalization_batching_http.py

"""HTTP integration tests batching финализации."""

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


class RecordingVisionModel:
    """Fake VLM, записывающая размеры finalization batches."""

    def __init__(
        self,
    ) -> None:
        """Инициализирует счётчики."""
        self.calls = 0

        self.batch_sizes: list[int] = []

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
        """Возвращает все findings текущего HTTP batch."""
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
                requested_num_predict=1800,
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
    """Создаёт HTTP payload одного source-less finding."""
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
    model: RecordingVisionModel,
):
    """Создаёт test application с batch_size=2."""
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
            num_predict=2600,
            max_issues=10,
            normative_text_limit=700,
        ),
        check_page_against_technical_assignment=(
            CheckPageAgainstTechnicalAssignment(
                vision_model=model,
                num_predict=2600,
                batch_size=20,
                requirement_text_limit=1800,
            )
        ),
        finalize_findings=FinalizeFindings(
            vision_model=model,
            num_predict=1800,
            batch_size=2,
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


async def test_finalize_endpoint_batches_five_findings() -> None:
    """HTTP endpoint использует 2+2+1, а не пять отдельных VLM calls."""
    model = RecordingVisionModel()

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
                        6,
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
        == 5
    )

    assert [finding["finding_id"] for finding in payload["findings"]] == [
        "p1-f1",
        "p1-f2",
        "p1-f3",
        "p1-f4",
        "p1-f5",
    ]

    assert model.calls == 3

    assert model.batch_sizes == [
        2,
        2,
        1,
    ]

    metrics = payload["metrics"]

    assert metrics["effective_batch_size"] == 2

    assert metrics["initial_batch_count"] == 3

    assert metrics["vlm_call_count"] == 3

    assert metrics["successful_vlm_call_count"] == 3

    assert metrics["failed_vlm_call_count"] == 0

    assert metrics["fallback_count"] == 0
