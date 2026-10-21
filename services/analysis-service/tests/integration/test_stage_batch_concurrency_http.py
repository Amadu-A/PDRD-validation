# services/analysis-service/tests/integration/test_stage_batch_concurrency_http.py

"""Интеграционные тесты ограниченной параллельности PDF-этапов с общим VLM."""

import asyncio
import base64
from dataclasses import asdict, replace
from typing import Any
from uuid import (
    UUID,
    uuid4,
)

import httpx
from pdrd_analysis_service.application.limited_vision import LimitedVisionModel
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


async def test_independent_atomic_check_keeps_all_31_pages_without_model_findings():
    """Настоящий HTTP Check Norms сохраняет страницы и N-происхождение при нулевом ответе модели."""
    from datetime import UTC, datetime

    from pdrd_analysis_service.application.independent_normative import (
        IndependentNormativeReview,
    )
    from pdrd_analysis_service.infrastructure.normative_requirements import (
        HttpNormativeRequirementsReader,
    )

    from ..unit.test_analysis_use_cases import FakeVisionModel, page_facts
    from ..unit.test_independent_normative import (
        DOC,
        GENERATION,
        SECTION,
        page,
        requirement,
    )

    row = requirement()
    row["normative_status"]["assessed_on"] = datetime.now(UTC).date().isoformat()
    row["status_evidence"][0].update(
        valid_from="2000-01-01", valid_until="2999-12-31", confirmed_on="2000-01-01"
    )
    calls = []

    def knowledge(request):
        """Сетевой адаптер читает настоящий контракт полного поколения."""
        calls.append(request.url.path)
        if request.url.path.endswith("/documents"):
            return httpx.Response(
                200, json=[{"document_id": DOC, "section_id": SECTION}]
            )
        return httpx.Response(
            200,
            json={
                "document_id": DOC,
                "generation_id": GENERATION,
                "state": "ready",
                "source_sha256": "a" * 64,
                "extractor_version": "normative-native-v1",
                "status_evidence": row["status_evidence"],
                "count": 1,
                "offset": 0,
                "next_offset": None,
                "requirements": [row],
            },
        )

    model = FakeVisionModel(
        [{"summary": "Проверено", "violations": []} for _ in range(31)]
    )
    app = _build_app(
        model=model, progress_probe=RecordingProgressProbe(), concurrency=3
    )
    app.state.container = replace(
        app.state.container,
        independent_normative_review=IndependentNormativeReview(
            HttpNormativeRequirementsReader(
                "http://knowledge.test", transport=httpx.MockTransport(knowledge)
            )
        ),
    )
    initial = page()
    facts = initial.facts
    items = [
        {
            "page_number": number,
            "extracted_text": initial.text if number == 13 else f"Лист {number}",
            "page_facts": asdict(
                replace(page_facts(), document_facts=facts if number == 13 else ())
            ),
            "normative_sources": [],
            "image_base64": _image(),
        }
        for number in range(1, 32)
    ]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://analysis.test"
    ) as client:
        response = await client.post(
            "/internal/v1/stages/check-norms",
            json={
                "document_id": str(uuid4()),
                "normative_corpus": {"section_id": SECTION, "document_ids": [DOC]},
                "items": items,
            },
        )
        assert response.status_code == 200, response.text
        drafts = response.json()["items"][12]["result"]["findings"]
        final = await client.post(
            "/internal/v1/findings/finalize",
            json={"findings": drafts, "experience_by_finding": {}},
        )
        assert final.status_code == 200, final.text
        assert (
            final.json()["findings"][0]["basis_sources"][0]["atomic_requirement"][
                "requirement_id"
            ]
            == row["requirement_id"]
        )
    assert response.status_code == 200, response.text
    data = response.json()
    assert [item["page_number"] for item in data["items"]] == list(range(1, 32))
    assert model.call_count == 31
    findings = data["items"][12]["result"]["findings"]
    assert len(findings) == 1
    assert (
        findings[0]["basis_sources"][0]["atomic_requirement"]["requirement_id"]
        == row["requirement_id"]
    )
    assert all(
        not item["result"]["findings"]
        for index, item in enumerate(data["items"])
        if index != 12
    )
    assert (
        len(calls) == 2
    )  # Один корпус на документ, а не по одному запросу на каждый лист.


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
    """Имитирует проверку отмены долговечного задания."""

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

    assert response.status_code == 200
    assert response.json() == {"items": [], "cancelled": True}

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


async def test_two_hundred_page_stage_is_allowed_and_does_not_increase_concurrency() -> (
    None
):
    """Предел 200 проходит настоящий HTTP-этап, сохраняя параллельность четырёх VLM-вызовов."""
    model = ConcurrentPageVisionModel()
    app = _build_app(
        model=model, progress_probe=RecordingProgressProbe(), concurrency=4
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/internal/v1/stages/understand-pages",
            json=_request_payload(document_id=uuid4(), pages=200),
        )
    assert response.status_code == 200
    assert [row["page_number"] for row in response.json()["items"]] == list(
        range(1, 201)
    )
    assert sorted(model.page_calls) == list(range(1, 201))
    assert model.max_active == 4


async def test_over_limit_stage_is_rejected_before_any_vlm_call() -> None:
    """Защита внутри Analysis Service не начинает дорогостоящие вызовы для 201 страницы."""
    model = ConcurrentPageVisionModel()
    app = _build_app(
        model=model, progress_probe=RecordingProgressProbe(), concurrency=4
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/internal/v1/stages/understand-pages",
            json=_request_payload(document_id=uuid4(), pages=201),
        )
    assert response.status_code == 413
    assert model.page_calls == []


async def test_cad_page_header_uses_durable_cancellation_before_provider() -> None:
    """Page API CAD использует идентичность задания и штатную отмену, как PDF stage."""
    provider = ConcurrentPageVisionModel()
    probe = RecordingProgressProbe(cancel_on_call=1)
    app = _build_app(
        model=LimitedVisionModel(provider, 1), progress_probe=probe, concurrency=1
    )
    document_id = uuid4()
    item = _request_payload(document_id=document_id, pages=1)["items"][0]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/internal/v1/pages/understand",
            json=item,
            headers={"X-PDRD-Analysis-Document-ID": str(document_id)},
        )
    assert response.status_code == 200 and response.json()["cancelled"] is True
    assert provider.page_calls == []
    assert probe.calls[0][:2] == (document_id, "understanding_sheet")


async def test_t_first_cancellation_reports_its_own_stage() -> None:
    """Отмена проверки ТЗ не помечается Understanding и не начинает GPU-вызов."""
    model = ConcurrentPageVisionModel()
    probe = RecordingProgressProbe(cancel_on_call=1)
    app = _build_app(model=model, progress_probe=probe, concurrency=1)
    document_id = uuid4()
    payload = {
        "document_id": str(document_id),
        "technical_assignment_id": str(uuid4()),
        "analysis_document_id": str(uuid4()),
        "section_id": str(uuid4()),
        "source_file": "assignment.pdf",
        "source_sha256": "a" * 64,
        "requirements": [
            {
                "point_id": "point-1",
                "requirement_id": "R1",
                "requirement_index": 1,
                "page": 1,
                "requirement_strength": "explicit",
                "source_text": "Требование",
                "text": "Требование",
            }
        ],
        "items": [
            {
                "page_number": 1,
                "extracted_text": "Лист",
                "image_base64": _image(),
                "page_facts": {
                    "discipline": "ВК",
                    "page_type": "drawing",
                    "summary": "Лист",
                    "objects": [],
                    "connections": [],
                    "labels": [],
                    "normative_queries": [],
                },
            }
        ],
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/internal/v1/stages/check-technical-assignment", json=payload
        )
    assert response.status_code == 200 and response.json() == {
        "items": [],
        "cancelled": True,
    }
    assert probe.calls[0][:2] == (document_id, "checking_technical_assignment")
    assert model.page_calls == []


async def test_cancellation_does_not_hide_parallel_provider_failure() -> None:
    """Настоящая ошибка другого уже работающего элемента сохраняется при одновременной отмене."""
    import pytest
    from fastapi import HTTPException
    from pdrd_analysis_service.application.vision_execution import (
        AnalysisCancelledError,
    )
    from pdrd_analysis_service.transport.http.stage_batch_routes import _run_stage_items

    app = _build_app(
        model=ConcurrentPageVisionModel(), progress_probe=None, concurrency=2
    )
    ready = asyncio.Event()
    started = 0

    async def process(item):
        """Запускает оба элемента до передачи штатной отмены и настоящего отказа провайдера."""
        nonlocal started
        started += 1
        if started == 2:
            ready.set()
        await ready.wait()
        if item == 1:
            raise AnalysisCancelledError("Отмена")
        await asyncio.sleep(0)
        raise HTTPException(503, detail="Провайдер недоступен")

    with pytest.raises(HTTPException) as caught:
        await asyncio.wait_for(
            _run_stage_items(
                container=app.state.container,
                document_id=uuid4(),
                stage="checking_requirements",
                items=[1, 2],
                process=process,
            ),
            1,
        )
    assert caught.value.status_code == 503 and started == 2
