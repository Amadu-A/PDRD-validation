# services/analysis-service/tests/integration/test_understanding_budget_http.py

"""Настоящий маршрут Understanding и vLLM-адаптер: переполнение, разбиение и отмена."""

import base64
import json
from uuid import uuid4

import httpx
import pytest

from .test_normative_budget_http import CancellationProbe, application


@pytest.mark.parametrize("cancelled", [False, True])
async def test_http_budget_recovers_independent_fields_without_same_schema_retry(
    monkeypatch, cancelled
):
    """Обрезанный JSON не принимается, отмена останавливает дополнительные GPU-запросы."""
    calls = []

    def completion(request):
        """Имитирует протокол shared-vlm, сохраняя настоящий парсер ответа."""
        payload = json.loads(request.content)
        calls.append(payload)
        fields = payload["response_format"]["json_schema"]["schema"]["properties"]
        if len(calls) == 1:
            content, reason = '{"summary":"НЕПОЛНЫЙ ОТВЕТ', "length"
        else:
            content = json.dumps(
                {
                    name: [] if schema.get("type") == "array" else "Схема"
                    for name, schema in fields.items()
                },
                ensure_ascii=False,
            )
            reason = "stop"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}, "finish_reason": reason}],
                "usage": {
                    "prompt_tokens": 5052,
                    "completion_tokens": 1600 if reason == "length" else 200,
                },
            },
        )

    real_client = httpx.AsyncClient
    async with real_client(
        transport=httpx.ASGITransport(app=application(CancellationProbe(cancelled))),
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
            "/internal/v1/stages/understand-pages",
            json={
                "document_id": str(uuid4()),
                "items": [
                    {
                        "page_number": 20,
                        "heuristic_page_type": "unknown",
                        "extracted_text": "План расстановки оборудования",
                        "image_base64": base64.b64encode(b"image").decode(),
                        "use_document_context": True,
                        "use_equipment_web_search": True,
                    }
                ],
            },
        )
    if cancelled:
        assert response.status_code == 200
        assert response.json() == {"items": [], "cancelled": True}
        assert len(calls) == 1
    else:
        assert response.status_code == 200
        facts = response.json()["items"][0]["result"]["facts"]
        assert (
            facts["summary"] == "Схема"
            and facts["document_facts"] == []
            and facts["equipment_identities"] == []
        )
        assert len(calls) == 4
        schemas = [
            set(call["response_format"]["json_schema"]["schema"]["properties"])
            for call in calls
        ]
        assert schemas[2] == {"document_facts"} and schemas[3] == {
            "equipment_identities"
        }
        assert schemas[0] != schemas[1]
