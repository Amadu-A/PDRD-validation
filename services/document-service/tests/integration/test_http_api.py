# services/document-service/tests/integration/test_http_api.py

"""HTTP integration tests Document Service."""

import base64

import fitz
from fastapi.testclient import TestClient
from pdrd_document_service.application.use_cases.extract import (
    ExtractPdfDocument,
)
from pdrd_document_service.core.container import (
    ApplicationContainer,
)
from pdrd_document_service.core.settings import (
    PdfSettings,
    Settings,
)
from pdrd_document_service.infrastructure.pdf.pymupdf import (
    PyMuPdfReader,
)
from pdrd_document_service.main import create_app


def build_pdf() -> bytes:
    """Создаёт тестовый PDF в памяти."""
    document = fitz.open()

    first_page = document.new_page()

    first_page.insert_text(
        (
            72,
            72,
        ),
        "First page",
    )

    second_page = document.new_page()

    second_page.insert_text(
        (
            72,
            72,
        ),
        "Second page",
    )

    content = document.tobytes()

    document.close()

    return content


def build_client() -> TestClient:
    """Создаёт Document Service test client."""
    pdf_settings = PdfSettings(
        max_upload_mb=10,
        render_max_side=1000,
        max_analysis_pages=10,
        text_limit=12000,
    )

    settings = Settings(
        _env_file=None,
        service_name="PDRD Document Service Test",
        service_version="0.1.0-test",
        environment="test",
        pdf=pdf_settings,
    )

    reader = PyMuPdfReader(
        render_max_side=(pdf_settings.render_max_side),
        text_limit=pdf_settings.text_limit,
    )

    container = ApplicationContainer(
        settings=settings,
        extract_pdf=ExtractPdfDocument(
            reader=reader,
            max_upload_bytes=(pdf_settings.max_upload_bytes),
            max_analysis_pages=(pdf_settings.max_analysis_pages),
        ),
    )

    return TestClient(
        create_app(
            container=container,
        )
    )


def test_health_endpoints() -> None:
    """Проверяет health contracts."""
    with build_client() as client:
        live_response = client.get(
            "/health/live",
        )

        ready_response = client.get(
            "/health/ready",
        )

    assert live_response.status_code == 200

    assert live_response.json() == {
        "status": "ok",
        "service": "PDRD Document Service Test",
        "version": "0.1.0-test",
    }

    assert ready_response.status_code == 200

    assert ready_response.json() == {
        "status": "ready",
        "service": "PDRD Document Service Test",
        "version": "0.1.0-test",
        "capabilities": {
            "pdf": True,
            "dxf": False,
            "dwg": False,
        },
    }


def test_extract_pdf_endpoint() -> None:
    """Проверяет полный HTTP PDF extraction contract."""
    with build_client() as client:
        response = client.post(
            "/internal/v1/pdf/extract",
            files={
                "file": (
                    "test.pdf",
                    build_pdf(),
                    "application/pdf",
                ),
            },
            data={
                "pages": "2",
            },
        )

    assert response.status_code == 200

    payload = response.json()

    assert payload["file_name"] == "test.pdf"
    assert payload["total_pages"] == 2
    assert payload["selected_pages"] == [
        2,
    ]

    assert (
        len(
            payload["pages"],
        )
        == 1
    )

    page = payload["pages"][0]

    assert page["page_number"] == 2
    assert "Second page" in page["text"]

    png = base64.b64decode(
        page["image_base64"],
    )

    assert png.startswith(
        b"\x89PNG\r\n\x1a\n",
    )


def test_extract_pdf_rejects_invalid_page() -> None:
    """Проверяет validation page selection через HTTP."""
    with build_client() as client:
        response = client.post(
            "/internal/v1/pdf/extract",
            files={
                "file": (
                    "test.pdf",
                    build_pdf(),
                    "application/pdf",
                ),
            },
            data={
                "pages": "100",
            },
        )

    assert response.status_code == 422
