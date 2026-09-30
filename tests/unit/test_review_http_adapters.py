# tests/unit/test_review_http_adapters.py

"""Регрессии закрытого HTTP: недоступность, ошибочный JSON и безопасные сообщения."""

from uuid import uuid4

import httpx
import pytest
from pdrd_api_gateway.application.ports.review import ReviewContext, ReviewRequestError
from pdrd_api_gateway.infrastructure.review import HttpReviewService
from pdrd_experience_service.application.ports.analysis_source import (
    AnalysisSourceUnavailableError,
)
from pdrd_experience_service.domain.review import ReviewError
from pdrd_experience_service.infrastructure.analysis.http_source import (
    GatewayAnalysisSource,
)


def failing_transport(mode):
    """Имитирует реальные ошибки upstream без запуска внешнего сервиса."""

    def handle(request):
        """Возвращает неверный HTTP-контракт или прерывает соединение."""
        if mode == "network":
            raise httpx.ConnectError("private upstream host/key", request=request)
        if mode == "html":
            return httpx.Response(200, text="<html>proxy failure</html>")
        if mode == "list":
            return httpx.Response(200, json=[])
        if mode == "wrong-job":
            return httpx.Response(200, json={"job_id": str(uuid4())})
        return httpx.Response(502, text="private upstream details")

    return httpx.MockTransport(handle)


@pytest.mark.parametrize("mode", ["network", "html", "list", "wrong-job", "status"])
async def test_experience_failure_is_safe_503_at_gateway(mode):
    """Ошибочный upstream не выдаёт ложный успех или сетевые детали браузеру."""
    service = HttpReviewService(
        "http://experience", "private-key", transport=failing_transport(mode)
    )
    with pytest.raises(ReviewRequestError) as caught:
        await service.execute(context=ReviewContext("engineer:test", uuid4(), "open"))
    assert caught.value.status_code == 503
    assert "private" not in caught.value.detail
    assert "private-key" not in repr(service)


@pytest.mark.parametrize("mode", ["network", "html", "list", "wrong-job", "status"])
async def test_source_failure_does_not_create_review(mode):
    """Некорректный источник остаётся ошибкой загрузки, не пустым успешным Review."""
    source = GatewayAnalysisSource(
        "http://gateway", "private-key", transport=failing_transport(mode)
    )
    with pytest.raises(AnalysisSourceUnavailableError) as caught:
        await source.load_completed(uuid4())
    assert "private" not in str(caught.value)
    assert "private-key" not in repr(source)


async def test_source_preserves_safe_domain_error_instead_of_masking_it_as_503():
    """Отсутствующий PDF объясняется инженеру, сохраняя статус ошибки команды."""
    transport = httpx.MockTransport(
        lambda _: httpx.Response(422, json={"detail": "Требуется исходный PDF."})
    )
    source = GatewayAnalysisSource("http://gateway", "private-key", transport=transport)
    with pytest.raises(ReviewError, match="Требуется исходный PDF"):
        await source.load_completed(uuid4())
