# services/api-gateway/tests/unit/test_pdf_export_transport.py

"""Классификация PDF-ошибок на межсервисной границе без раскрытия внутреннего ответа."""

import httpx
import pytest
from pdrd_api_gateway.application.ports.analysis_pdf_export import (
    AnalysisPdfExportError,
    AnalysisPdfReport,
)
from pdrd_api_gateway.core.settings import DocumentServiceSettings
from pdrd_api_gateway.infrastructure.analysis_pdf_export import (
    DocumentServiceAnalysisAnnotatedPdfRenderer,
)


@pytest.mark.parametrize(
    "status,expected,code",
    [
        (400, 422, "pdf_invalid_input"),
        (413, 422, "pdf_invalid_input"),
        (422, 422, "pdf_invalid_input"),
        (500, 502, "pdf_generation_failed"),
        (503, 503, "pdf_service_unavailable"),
    ],
)
async def test_upstream_status_is_classified_and_request_id_is_preserved(
    status, expected, code
):
    """Секретный текст upstream не входит в публичную ошибку; запрос содержит JSON-файл."""
    requests = []

    async def handler(request):
        """Проверяет фактический multipart и ответ отдельного сервиса."""
        requests.append(request)
        assert b'filename="annotations.json"' in await request.aread()
        return httpx.Response(status, text="SECRET-INTERNAL-DOCUMENT")

    renderer = DocumentServiceAnalysisAnnotatedPdfRenderer(
        settings=DocumentServiceSettings(), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(AnalysisPdfExportError) as caught:
        await renderer.render(
            pdf_content=b"%PDF-test",
            file_name="source.pdf",
            annotations=(),
            report=AnalysisPdfReport("Отчёт", (), "", (), ()),
        )
    error = caught.value
    assert (error.status_code, error.code) == (expected, code)
    assert error.request_id == requests[0].headers["X-Request-ID"]
    assert "SECRET" not in str(error)


@pytest.mark.parametrize(
    "failure,expected,code",
    [
        (httpx.ReadTimeout, 504, "pdf_timeout"),
        (httpx.ConnectError, 503, "pdf_service_unavailable"),
    ],
)
async def test_transport_errors_are_distinguished(failure, expected, code):
    """Тайм-аут не выдаётся за неверный PDF и не раскрывает внутренние адреса."""

    async def handler(request):
        """Воспроизводит сбой без запроса к рабочему сервису."""
        raise failure("SECRET-INTERNAL-HOST", request=request)

    renderer = DocumentServiceAnalysisAnnotatedPdfRenderer(
        settings=DocumentServiceSettings(), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(AnalysisPdfExportError) as caught:
        await renderer.render(
            pdf_content=b"%PDF-test",
            file_name="source.pdf",
            annotations=(),
            report=AnalysisPdfReport("Отчёт", (), "", (), ()),
        )
    assert (caught.value.status_code, caught.value.code) == (expected, code)
    assert "SECRET" not in str(caught.value)
