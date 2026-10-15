# services/analysis-service/tests/integration/test_understanding_budget_http.py

"""Настоящие HTTP-маршруты и vLLM-адаптер: overflow без recovery, Check Norms всех листов и отмена."""

import base64
import json
from uuid import uuid4

import httpx
import pytest

from .test_normative_budget_http import CancellationProbe, application


@pytest.mark.parametrize("cancelled", [False, True])
async def test_http_overflow_does_not_recover_and_honours_cancellation(
    monkeypatch, cancelled
):
    """Обрезанный JSON от настоящего адаптера не вызывает повторное распознавание."""
    calls = []

    def completion(request):
        """Сохраняет запрос на сетевой границе и возвращает исчерпание вывода."""
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": '{"summary":"ОБРЕЗАННЫЙ ОТВЕТ'},
                        "finish_reason": "length",
                    }
                ],
                "usage": {"prompt_tokens": 5052, "completion_tokens": 1600},
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
    assert response.status_code == 200 and len(calls) == 1
    if cancelled:
        assert response.json() == {"items": [], "cancelled": True}
    else:
        result = response.json()["items"][0]["result"]
        assert result["facts"]["summary"] == ""
        assert (
            result["facts"]["document_facts"]
            == result["facts"]["equipment_identities"]
            == []
        )
        diagnostic = result["metrics"]["understanding"]
        assert (
            diagnostic["recovery_count"] == 0 and diagnostic["main_request_count"] == 1
        )
        assert (
            diagnostic["limited_reason"] == "output_budget"
            and diagnostic["result"] == "incomplete"
        )
        assert (
            diagnostic["input_tokens"] == 5052 and diagnostic["output_tokens"] == 1600
        )


@pytest.mark.parametrize("d", [False, True])
async def test_all_31_pages_continue_to_norms_with_original_text_and_image(
    monkeypatch, d
):
    """Переполнение каждого листа сохраняет PAGE TEXT, изображение и все страницы без фиктивных источников."""
    understanding_calls, normative_calls = [], []
    image = base64.b64encode(b"original-page-image").decode()

    def completion(request):
        """Настоящий адаптер получает length в Understanding и нормальный ответ в Check Norms."""
        data = json.loads(request.content)
        schema = data["response_format"]["json_schema"]["schema"]
        if "violations" not in schema["properties"]:
            understanding_calls.append(data)
            content, reason = '{"summary":"ОБРЫВ', "length"
        else:
            normative_calls.append(data)
            content, reason = (
                json.dumps(
                    {"summary": "Проверено по исходному листу", "violations": []}
                ),
                "stop",
            )
            serialized = json.dumps(data["messages"], ensure_ascii=False)
            assert "Расчётный расход принят 10 л/с" in serialized
            assert f"data:image/png;base64,{image}" in serialized
            assert "ОБРЫВ" not in serialized
            assert "DOCUMENT CONTEXT SOURCES:" not in serialized
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}, "finish_reason": reason}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 1600 if reason == "length" else 20,
                },
            },
        )

    job = str(uuid4())
    items = [
        {
            "page_number": page,
            "heuristic_page_type": "text",
            "extracted_text": f"Страница {page}: Расчётный расход принят 10 л/с.",
            "image_base64": image,
            "use_document_context": d,
            "use_equipment_web_search": True,
        }
        for page in range(1, 32)
    ]
    real_client = httpx.AsyncClient
    async with real_client(
        transport=httpx.ASGITransport(app=application(CancellationProbe(False))),
        base_url="http://test",
    ) as client:
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda **kwargs: real_client(
                transport=httpx.MockTransport(completion), **kwargs
            ),
        )
        understood = await client.post(
            "/internal/v1/stages/understand-pages",
            json={"document_id": job, "items": items},
        )
        assert understood.status_code == 200, understood.text
        pages = understood.json()["items"]
        assert [item["page_number"] for item in pages] == list(range(1, 32))
        for item in pages:
            facts = item["result"]["facts"]
            assert item["result"]["metrics"]["understanding"]["job"] == job
            assert facts["document_facts"][0]["value_raw"] == "10"
            assert facts["document_facts"][0]["extraction_method"] == "pdf_text"
            assert facts["equipment_identities"] == facts["normative_queries"] == []
        checked = await client.post(
            "/internal/v1/stages/check-norms",
            json={
                "document_id": job,
                "items": [
                    {
                        "page_number": item["page_number"],
                        "extracted_text": original["extracted_text"],
                        "page_facts": item["result"]["facts"],
                        "image_base64": image,
                        "normative_sources": [],
                        "technical_assignment_sources": [],
                        "user_package_sources": [],
                        "document_context_sources": [],
                    }
                    for item, original in zip(pages, items, strict=True)
                ],
            },
        )
        assert checked.status_code == 200, checked.text
        assert len(checked.json()["items"]) == 31
        assert all(not item["result"]["findings"] for item in checked.json()["items"])
    assert len(understanding_calls) == len(normative_calls) == 31


async def test_http_transport_failure_is_not_a_successful_empty_analysis(monkeypatch):
    """503 общей модели остаётся ошибкой инфраструктуры без подстановки пустых PAGE FACTS."""
    real_client = httpx.AsyncClient
    async with real_client(
        transport=httpx.ASGITransport(app=application(CancellationProbe(False))),
        base_url="http://test",
    ) as client:
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda **kwargs: real_client(
                transport=httpx.MockTransport(lambda request: httpx.Response(503)),
                **kwargs,
            ),
        )
        response = await client.post(
            "/internal/v1/stages/understand-pages",
            json={
                "document_id": str(uuid4()),
                "items": [
                    {
                        "page_number": 1,
                        "heuristic_page_type": "text",
                        "extracted_text": "Исходный лист",
                        "image_base64": base64.b64encode(b"image").decode(),
                    }
                ],
            },
        )
    assert response.status_code == 503
