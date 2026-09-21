# services/analysis-service/tests/integration/test_stage_batch_concurrency_http.py

"""Integration tests bounded concurrent shared-vlm PDF stages."""

import asyncio
import base64
from typing import Any
from uuid import (
    UUID,
    uuid4,
)

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
    PipelineSettings,
    Settings,
)
from pdrd_analysis_service.domain.analysis import (
    GenerationMetrics,
    GenerationResult,
)
from pdrd_analysis_service.main import (
    create_app,
)


class ConcurrentPageVisionModel:
    """Fake VLM с наблюдаемой конкурентностью."""

    def __init__(
        self,
    ) -> None:
        """Инициализирует counters."""
        self.active = 0
        self.max_active = 0

        self.page_calls: list[int] = []

        self._lock = asyncio.Lock()

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
        """Возвращает deterministic PageFacts после короткого await."""
        del prompt
        del schema
        del num_predict
        del seed

        assert image_bytes is not None

        assert stage.startswith(
            "page_understanding:",
        )

        page_number = int(
            stage.rsplit(
                ":",
                maxsplit=1,
            )[1]
        )

        async with self._lock:
            self.active += 1

            self.max_active = max(
                self.max_active,
                self.active,
            )

            self.page_calls.append(
                page_number,
            )

        try:
            await asyncio.sleep(
                0.03,
            )

        finally:
            async with self._lock:
                self.active -= 1

        return GenerationResult(
            payload={
                "discipline": "ЭОМ",
                "page_type": "drawing",
                "summary": (f"Лист {page_number}"),
                "objects": [],
                "connections": [],
                "labels": [],
                "normative_queries": [],
            },
            metrics=GenerationMetrics(
                attempt=1,
                done_reason="stop",
                requested_num_predict=1600,
                total_duration_ms=10.0,
                load_duration_ms=0.0,
                prompt_eval_count=100,
                eval_count=50,
                content_length=100,
                thinking_length=0,
            ),
        )

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает readiness."""
        return True


class RecordingProgressProbe:
    """Fake durable cancellation probe."""

    def __init__(
        self,
        *,
        cancel_on_call: int | None = None,
    ) -> None:
        """Настраивает момент cancellation."""
        self.cancel_on_call = cancel_on_call

        self.calls: list[
            tuple[
                UUID,
                str,
                int,
                int,
            ]
        ] = []

    async def is_cancelled(
        self,
        *,
        document_id: UUID,
        stage: str,
        current: int,
        total: int,
    ) -> bool:
        """Возвращает cancellation на configured checkpoint."""
        self.calls.append(
            (
                document_id,
                stage,
                current,
                total,
            )
        )

        return (
            self.cancel_on_call is not None
            and len(
                self.calls,
            )
            >= self.cancel_on_call
        )


def _build_app(
    *,
    model: ConcurrentPageVisionModel,
    progress_probe: RecordingProgressProbe,
    concurrency: int,
):
    """Создаёт test application."""
    settings = Settings(
        _env_file=None,
        service_name=("PDRD Analysis Service Test"),
        service_version="0.1.0-test",
        environment="test",
        pipeline=PipelineSettings(
            vlm_stage_concurrency=(concurrency),
        ),
    )

    container = ApplicationContainer(
        settings=settings,
        understand_page=UnderstandPage(
            vision_model=model,
            num_predict=1600,
        ),
        build_normative_queries=(
            BuildNormativeQueries(
                max_queries=7,
            )
        ),
        check_page_against_norms=(
            CheckPageAgainstNorms(
                vision_model=model,
                num_predict=14000,
                max_issues=50,
                normative_text_limit=700,
            )
        ),
        check_page_against_technical_assignment=(
            CheckPageAgainstTechnicalAssignment(
                vision_model=model,
                num_predict=4000,
                batch_size=48,
                requirement_text_limit=1800,
            )
        ),
        finalize_findings=(
            FinalizeFindings(
                vision_model=model,
                num_predict=4000,
                batch_size=10,
                experience_context_limit=600,
                experience_min_score=0.55,
            )
        ),
        localize_findings=LocalizeFindings(
            vision_model=model,
            num_predict=4000,
        ),
        check_readiness=CheckReadiness(
            vision_model=model,
        ),
        analysis_progress_probe=(progress_probe),
    )

    return create_app(
        container=container,
    )


def _image() -> str:
    """Возвращает fake image Base64."""
    return base64.b64encode(
        b"fake-png",
    ).decode(
        "ascii",
    )


def _request_payload(
    *,
    document_id: UUID,
    pages: int,
) -> dict[str, Any]:
    """Строит multi-page stage request."""
    return {
        "document_id": str(
            document_id,
        ),
        "items": [
            {
                "page_number": page_number,
                "heuristic_page_type": "unknown",
                "extracted_text": (f"Текст листа {page_number}"),
                "image_base64": _image(),
            }
            for page_number in range(
                1,
                pages + 1,
            )
        ],
    }


async def test_understanding_stage_uses_bounded_concurrency_and_ordered_results() -> (
    None
):
    """Stage использует shared-vlm параллельно, но response остаётся ordered."""
    model = ConcurrentPageVisionModel()

    progress_probe = RecordingProgressProbe()

    app = _build_app(
        model=model,
        progress_probe=progress_probe,
        concurrency=3,
    )

    document_id = uuid4()

    transport = httpx.ASGITransport(
        app=app,
    )

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.post(
            ("/internal/v1/stages/understand-pages"),
            json=_request_payload(
                document_id=document_id,
                pages=6,
            ),
        )

    assert response.status_code == 200

    payload = response.json()

    assert [item["page_number"] for item in payload["items"]] == [
        1,
        2,
        3,
        4,
        5,
        6,
    ]

    assert sorted(
        model.page_calls,
    ) == [
        1,
        2,
        3,
        4,
        5,
        6,
    ]

    assert model.max_active == 3

    assert [call[2] for call in (progress_probe.calls)] == [
        1,
        2,
        3,
        4,
        5,
        6,
    ]


async def test_cancellation_stops_new_items_but_allows_inflight_calls_to_finish() -> (
    None
):
    """Cancellation не стартует queued pages после обнаружения stop."""
    model = ConcurrentPageVisionModel()

    progress_probe = RecordingProgressProbe(
        cancel_on_call=3,
    )

    app = _build_app(
        model=model,
        progress_probe=progress_probe,
        concurrency=3,
    )

    transport = httpx.ASGITransport(
        app=app,
    )

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.post(
            ("/internal/v1/stages/understand-pages"),
            json=_request_payload(
                document_id=uuid4(),
                pages=8,
            ),
        )

    assert response.status_code == 409

    assert response.json()["detail"]["code"] == "analysis_cancelled"

    assert sorted(
        model.page_calls,
    ) == [
        1,
        2,
    ]

    assert model.active == 0
    assert model.max_active == 2

    assert all(
        page_number <= 3
        for (
            _,
            _,
            page_number,
            _,
        ) in progress_probe.calls
    )
