# services/analysis-service/tests/integration/test_normative_budget_http.py

"""Production HTTP → discovery → vLLM восстанавливает ограниченный ответ и отмену."""

import base64
import json
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pdrd_analysis_service.application.use_cases import (
    BuildNormativeQueries,
    CheckReadiness,
    FinalizeFindings,
    LocalizeFindings,
    UnderstandPage,
)
from pdrd_analysis_service.application.use_cases.normative import CheckPageAgainstNorms
from pdrd_analysis_service.application.use_cases.technical_assignment_validation import (
    CheckPageAgainstTechnicalAssignment,
)
from pdrd_analysis_service.core.container import ApplicationContainer
from pdrd_analysis_service.core.settings import Settings
from pdrd_analysis_service.infrastructure.vllm import VllmStructuredVisionModel
from pdrd_analysis_service.transport.http.stage_batch_routes import router


def violation(index):
    """Независимое маркировочное замечание связано с реальным входным ID источника."""
    return {
        "category": "marking",
        "severity": "error",
        "status": "needs_review",
        "comment": f"Отсутствует маркировка узла {index}",
        "evidence": f"Узел {index} без обозначения",
        "object_ref": f"Узел {index}",
        "recommendation_draft": "",
        "confidence": 0.8,
        "normative_source_ids": ["N1"],
        "technical_assignment_source_ids": [],
        "user_package_source_ids": [],
        "document_context_source_ids": [],
        "visual_regions": [],
    }


class CancellationProbe:
    """Отменяет второй контроль: перед первой порцией отмены ещё нет."""

    def __init__(self, cancelled):
        """Фиксирует контрольные точки без обращения в Gateway."""
        self.cancelled, self.calls = cancelled, 0

    async def is_cancelled(self, **kwargs):
        """Возвращает отмену только перед продолжением текущего листа."""
        self.calls += 1
        return self.cancelled and self.calls >= 2


def application(probe):
    """Использует настоящие маршрут, сценарий и OpenAI-совместимый адаптер."""
    model = VllmStructuredVisionModel(
        base_url="http://shared-vlm:8000/v1",
        model="shared-vlm",
        request_timeout_seconds=30,
        connect_timeout_seconds=5,
        health_timeout_seconds=5,
        max_attempts=1,
        retry_backoff_seconds=0,
        max_retry_num_predict=14000,
    )
    app = FastAPI()
    app.state.container = ApplicationContainer(
        settings=Settings(_env_file=None),
        analysis_progress_probe=probe,
        check_page_against_norms=CheckPageAgainstNorms(model, 14000, 50, 700),
        understand_page=UnderstandPage(model, 1600),
        build_normative_queries=BuildNormativeQueries(7),
        check_page_against_technical_assignment=CheckPageAgainstTechnicalAssignment(
            model, 4000, 48, 1800
        ),
        finalize_findings=FinalizeFindings(model, 4000, 10, 600, 0.55),
        localize_findings=LocalizeFindings(model, 4000),
        check_readiness=CheckReadiness(model),
    )
    app.include_router(router)
    return app


def request_payload():
    """Сохраняет полный нормативный контекст страницы при уменьшении ответа."""
    return {
        "document_id": str(uuid4()),
        "items": [
            {
                "page_number": 22,
                "extracted_text": "Узлы 0—11",
                "page_facts": {
                    "discipline": "ОВ",
                    "page_type": "drawing",
                    "summary": "Узлы",
                    "objects": [],
                    "connections": [],
                    "labels": [],
                    "normative_queries": [],
                },
                "normative_sources": [
                    {"source_id": "N1", "score": 0.8, "text": "Норматив о маркировке"}
                ],
                "image_base64": base64.b64encode(b"image").decode("ascii"),
            }
        ],
    }


@pytest.mark.parametrize("cancelled", [False, True])
async def test_http_budget_failure_splits_schema_without_partial_json_or_blind_retry(
    monkeypatch, cancelled
):
    """Проба сохраняется; 10→5 меняет prompt/schema; отмена не делает новый GPU-вызов."""
    calls = []

    def completion(request):
        """Граница сети отвечает обрезанным JSON только первой bulk-порции."""
        payload = json.loads(request.content)
        calls.append(payload)
        capacity = payload["response_format"]["json_schema"]["schema"]["properties"][
            "violations"
        ]["maxItems"]
        assert capacity == (5 if len(calls) == 3 else 10)
        assert "Норматив о маркировке" in json.dumps(payload, ensure_ascii=False)
        if len(calls) == 2:
            content, reason = '{"violations":[{"comment":"НЕПОЛНЫЙ КАНДИДАТ"', "length"
        else:
            indexes = range(10) if len(calls) == 1 else range(10, 12)
            content = json.dumps(
                {
                    "summary": "Проверка узлов",
                    "violations": [violation(i) for i in indexes],
                },
                ensure_ascii=False,
            )
            reason = "stop"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}, "finish_reason": reason}],
                "usage": {
                    "prompt_tokens": 17486,
                    "completion_tokens": 14000 if reason == "length" else 1000,
                },
            },
        )

    probe = CancellationProbe(cancelled)
    real_client = httpx.AsyncClient
    async with real_client(
        transport=httpx.ASGITransport(app=application(probe)), base_url="http://test"
    ) as client:
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda **kwargs: real_client(
                transport=httpx.MockTransport(completion), **kwargs
            ),
        )
        response = await client.post(
            "/internal/v1/stages/check-norms", json=request_payload()
        )
    if cancelled:
        assert (
            response.status_code == 200
            and response.json()["cancelled"] is True
            and response.json()["items"] == []
        )
        assert len(calls) == 1
    else:
        assert response.status_code == 200
        result = response.json()["items"][0]["result"]
        assert len(result["findings"]) == 12
        assert all(
            finding["normative_source_ids"] == ["N1"] for finding in result["findings"]
        )
        assert "НЕПОЛНЫЙ КАНДИДАТ" not in json.dumps(result, ensure_ascii=False)
        assert [
            call["response_format"]["json_schema"]["schema"]["properties"][
                "violations"
            ]["maxItems"]
            for call in calls
        ] == [10, 10, 5]
        assert calls[1]["messages"] != calls[2]["messages"]
        assert [call["max_tokens"] for call in calls] == [4000, 14000, 14000]
