# services/analysis-service/tests/integration/test_shared_finalization_budget_http.py

"""Настоящие DTO, финализация и vLLM-адаптер сохраняют кандидатов после typed budget error."""

import json

import httpx
import pytest
from pdrd_analysis_service.transport.http.routes import router

from .test_finalization_batching_http import _finding_payload
from .test_normative_budget_http import CancellationProbe, application


@pytest.mark.parametrize("single", [False, True])
async def test_shared_vlm_budget_splits_batch_or_returns_explicit_failure(
    monkeypatch, single
):
    """Общая ошибка shared-vlm разбивает порцию; один неразрешимый кандидат даёт HTTP 503."""
    calls = []

    def completion(request):
        """Эмулирует только сетевой ответ; парсер, разбиение и mapping используются настоящие."""
        payload = json.loads(request.content)
        fields = payload["response_format"]["json_schema"]["schema"]["properties"]
        ids = fields["findings"]["items"]["properties"]["finding_id"]["enum"]
        calls.append(list(ids))
        content = {
            "summary": "Проверка завершена",
            "findings": [
                {
                    "finding_id": identifier,
                    "comment": "Проверить решение",
                    "recommendation": "Сверить схему",
                    "experience_source_ids": [],
                    "normative_source_ids": [],
                }
                for identifier in ids
            ],
        }
        reason = "length" if len(ids) > 2 or single else "stop"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": json.dumps(content)},
                        "finish_reason": reason,
                    }
                ],
                "usage": {
                    "prompt_tokens": 500,
                    "completion_tokens": 4000 if reason == "length" else 200,
                },
            },
        )

    real_client = httpx.AsyncClient
    app = application(CancellationProbe(False))
    app.include_router(router)
    async with real_client(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda **kwargs: real_client(
                transport=httpx.MockTransport(completion), **kwargs
            ),
        )
        response = await client.post(
            "/internal/v1/findings/finalize",
            json={
                "findings": [_finding_payload(i) for i in range(1, 2 if single else 5)],
                "experience_by_finding": {},
                "normative_candidates": [],
                "normative_candidates_by_finding": {},
            },
        )
    if single:
        assert response.status_code == 503 and len(calls) == 1
        assert "output budget" in response.json()["detail"]
    else:
        assert response.status_code == 200 and [len(ids) for ids in calls] == [4, 2, 2]
        result = response.json()
        assert [finding["finding_id"] for finding in result["findings"]] == [
            f"p1-f{i}" for i in range(1, 5)
        ]
        assert result["metrics"]["batches"][0]["eval_count"] == 4000
