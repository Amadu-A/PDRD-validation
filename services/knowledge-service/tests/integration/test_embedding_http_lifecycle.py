# services/knowledge-service/tests/integration/test_embedding_http_lifecycle.py

"""HTTP-контракт отмены, диагностируемой перегрузки и повторного поиска в одном задании."""

from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pdrd_knowledge_service.application.embedding_execution import (
    current_embedding_execution,
)
from pdrd_knowledge_service.application.ports.embedding import (
    EmbeddingProviderError,
    EmbeddingTemporaryError,
)
from pdrd_knowledge_service.application.ports.multimodal_embedding import (
    MultimodalEmbeddingInput,
)
from pdrd_knowledge_service.infrastructure.embedding.request_runtime import (
    EmbeddingRequestPolicy,
    EmbeddingRequestRuntime,
)
from pdrd_knowledge_service.transport.http.embedding_lifecycle import (
    EmbeddingLifecycleMiddleware,
)

from .test_http_api import FakeEmbeddingProvider, build_client

DOCUMENT = "00000000-0000-0000-0000-000000000001"
JOB = "00000000-0000-0000-0000-000000000002"
HEADERS = {"X-PDRD-Document-ID": DOCUMENT, "X-PDRD-Stage": "requirements"}


def _app(*, failures=0, cancel=False):
    """Собирает настоящий HTTP middleware и runtime, заменяя только модель и владельца lifecycle."""
    app = FastAPI()
    calls = []
    runtime = EmbeddingRequestRuntime(
        EmbeddingRequestPolicy(max_attempts=3, backoff_seconds=0.001)
    )

    async def control(document_id):
        """Возвращает авторитетный job ID, а не доверяет входящему заголовку задания."""
        assert document_id == UUID(DOCUMENT)
        return {
            "job_id": JOB,
            "cancelled": cancel and bool(calls),
            "remaining_seconds": 60,
        }

    app.add_middleware(EmbeddingLifecycleMiddleware, check_control=control)

    async def send(batch, remaining):
        """Моделирует отказ без физической GPU-нагрузки."""
        calls.append(batch)
        if len(calls) <= failures:
            context = current_embedding_execution.get()
            context.metrics["http_503"] += 1
            raise EmbeddingTemporaryError("busy", status_code=503)
        return [[1.0] for _ in batch]

    @app.post("/search")
    async def search():
        """Возвращает те же исходные source/page IDs после embedding, включая кэш."""
        await runtime.execute(
            (MultimodalEmbeddingInput(text="вопрос"),), identity="model", send=send
        )
        return {"sources": [{"source_id": "N7", "point_id": "point", "page": 11}]}

    return app, calls


def test_http_transient_busy_recovers_and_job_cache_preserves_source_ids():
    """Два вызова страниц не теряют источники; повторное embedding внутри задания не выполняется."""
    app, calls = _app(failures=2)
    with TestClient(app) as client:
        first = client.post("/search", headers=HEADERS)
        second = client.post("/search", headers=HEADERS)
    assert first.status_code == second.status_code == 200
    assert first.json()["sources"] == second.json()["sources"]
    assert first.json()["retrieval_metrics"]["physical_requests"] == 3
    assert first.json()["retrieval_metrics"]["job_id"] == JOB
    assert second.json()["retrieval_metrics"]["physical_requests"] == 0
    assert second.json()["retrieval_metrics"]["cache_hits"] == 1
    assert len(calls) == 3


def test_http_persistent_busy_is_503_with_budget_reason_and_never_empty_success():
    """Незавершённый нормативный поиск нельзя принять за успешный поиск без норм."""
    app, calls = _app(failures=100)
    with TestClient(app) as client:
        response = client.post("/search", headers=HEADERS)
    body = response.json()
    assert response.status_code == 503 and not body["retrieval_complete"]
    assert body["detail"]["code"] == "embedding_budget_exhausted"
    assert "sources" not in body and len(calls) == 3
    assert body["retrieval_metrics"]["http_503"] == 3


def test_http_cancellation_is_explicit_and_stops_wait_without_error_status():
    """Ожидаемая отмена не делает красный HTTP-узел n8n и не считается завершённым поиском."""
    app, calls = _app(failures=100, cancel=True)
    with TestClient(app) as client:
        response = client.post("/search", headers=HEADERS)
    assert response.status_code == 200 and response.json()["cancelled"]
    assert not response.json()["retrieval_complete"] and len(calls) == 1


def test_requests_without_lifecycle_header_keep_previous_response_contract():
    """Старые внутренние клиенты и индексаторы не требуют фиктивного analysis job."""
    app, _ = _app()
    with TestClient(app) as client:
        response = client.post("/search")
    assert set(response.json()) == {"sources"}


def test_real_search_route_distinguishes_input_validation_from_overload(monkeypatch):
    """Постоянный отказ модели проходит через настоящий router как диагностируемый 422."""

    async def invalid(self, *args, **kwargs):
        """Не раскрывает исходный запрос из ответа сервера модели."""
        raise EmbeddingProviderError(
            "HTTP 400", reason_code="embedding_input_too_large", status_code=400
        )

    monkeypatch.setattr(FakeEmbeddingProvider, "embed", invalid)
    with build_client() as client:
        response = client.post(
            "/internal/v1/search/normative", json={"queries": ["вопрос"]}
        )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "embedding_input_too_large"
