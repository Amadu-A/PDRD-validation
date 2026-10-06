# services/api-gateway/tests/unit/test_pdf_selection.py

"""PDF-отказ сохраняется до очереди, без вызова n8n и потери текста ошибки."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from pdrd_api_gateway.application.ports.pdf_selection import (
    InvalidPdfSelectionError,
    PdfSelectionUnavailableError,
)
from pdrd_api_gateway.application.use_cases.submit_analysis import SubmitAnalysis
from pdrd_api_gateway.core.settings import DocumentServiceSettings
from pdrd_api_gateway.infrastructure.pdf_selection import HttpPdfSelectionValidator


@pytest.mark.asyncio
async def test_document_error_stops_job_before_persistence_and_queue():
    """201 страница не создаёт ни файл заявки, ни задание/outbox."""
    message = "Выбрано 201 страниц. Максимум 200. Укажите диапазон 1-200."

    def server(request):
        """Проверка идёт в Document Service, а не в workflow."""
        assert request.url.path == "/internal/v1/pdf/inspect"
        return httpx.Response(422, json={"detail": message})

    validator = HttpPdfSelectionValidator(
        DocumentServiceSettings(), httpx.MockTransport(server)
    )
    artifacts = SimpleNamespace(save_request=AsyncMock())
    jobs = SimpleNamespace(execute=AsyncMock())
    use_case = SubmitAnalysis(artifacts, jobs, pdf_selection=validator)
    with pytest.raises(InvalidPdfSelectionError, match="Максимум 200") as error:
        await use_case.execute(
            pdf_content=b"pdf",
            pdf_file_name="ВК.pdf",
            cad_content=None,
            cad_file_name=None,
            pages=None,
        )
    assert str(error.value) == message
    artifacts.save_request.assert_not_awaited()
    jobs.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,body", [(503, {"detail": "private address"}), (200, {}), (200, [])]
)
async def test_unavailable_or_malformed_document_reply_does_not_queue(status, body):
    """Неожиданный ответ не превращается в разрешение или раскрытие адресов инфраструктуры."""
    validator = HttpPdfSelectionValidator(
        DocumentServiceSettings(),
        httpx.MockTransport(lambda _: httpx.Response(status, json=body)),
    )
    with pytest.raises(
        PdfSelectionUnavailableError, match="Не удалось проверить"
    ) as error:
        await validator.validate(content=b"pdf", file_name="ВК.pdf", pages="1-200")
    assert "private address" not in str(error.value)


@pytest.mark.asyncio
async def test_valid_pdf_continues_to_owned_job_and_cad_does_not_inspect_pdf():
    """Успешная проверка не ломает обычный авторизованный PDF и самостоятельный CAD."""
    from uuid import uuid4

    from pdrd_api_gateway.domain.analysis_job import AnalysisJob

    owner = uuid4()
    requests = []

    def server(request):
        """Возвращает разрешённую границу Document Service."""
        requests.append(request)
        return httpx.Response(
            200, json={"selected_pages": list(range(1, 201)), "total_pages": 200}
        )

    validator = HttpPdfSelectionValidator(
        DocumentServiceSettings(), httpx.MockTransport(server)
    )
    artifacts = SimpleNamespace(save_request=AsyncMock())

    async def create(**kwargs):
        """Сохраняет подтверждённого владельца; имитирует существующее создание job/outbox."""
        return AnalysisJob.create(
            document_id=kwargs["document_id"], owner_user_id=kwargs["owner_user_id"]
        )

    jobs = SimpleNamespace(execute=AsyncMock(side_effect=create))
    use_case = SubmitAnalysis(artifacts, jobs, pdf_selection=validator)
    pdf_job = await use_case.execute(
        pdf_content=b"pdf",
        pdf_file_name="ВК.pdf",
        cad_content=None,
        cad_file_name=None,
        pages="1-200",
        owner_user_id=owner,
    )
    assert pdf_job.owner_user_id == owner and len(requests) == 1
    assert artifacts.save_request.await_args.kwargs["submission"].pages == "1-200"
    await use_case.execute(
        pdf_content=None,
        pdf_file_name=None,
        cad_content=b"cad",
        cad_file_name="ВК.dxf",
        pages=None,
        owner_user_id=owner,
    )
    assert len(requests) == 1 and jobs.execute.await_count == 2
