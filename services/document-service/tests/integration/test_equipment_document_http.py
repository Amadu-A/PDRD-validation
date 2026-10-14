# services/document-service/tests/integration/test_equipment_document_http.py

"""HTTP-проверки внутреннего извлечения документа производителя."""

import base64

import fitz
from fastapi.testclient import TestClient
from pdrd_document_service.main import create_app


def test_equipment_pdf_extract_returns_text_without_images() -> None:
    """API отдаёт физическую страницу и текст без полного визуального рендера."""
    document = fitz.open()
    document.new_page().insert_text((72, 72), "DRC-100B 27.6 V DC")
    content = document.tobytes()
    document.close()
    with TestClient(create_app()) as client:
        response = client.post(
            "/internal/v1/equipment-documents/extract",
            files={"file": ("manual.pdf", content, "application/pdf")},
            data={"media_type": "application/pdf"},
        )
    assert response.status_code == 200, response.text
    assert response.json()["pages"][0]["page"] == 1
    assert "DRC-100B" in response.json()["pages"][0]["text"]
    assert "image_base64" not in response.text


def test_targeted_render_returns_only_selected_pdf_page() -> None:
    """Растр отдельной страницы пригоден для адресного VLM и ограничен."""
    document = fitz.open()
    document.new_page().insert_text((72, 72), "Page 1")
    document.new_page().insert_text((72, 72), "Page 2")
    content = document.tobytes()
    document.close()
    with TestClient(create_app()) as client:
        response = client.post(
            "/internal/v1/equipment-documents/render-page",
            files={"file": ("manual.pdf", content, "application/pdf")},
            data={"page_number": "2"},
        )
        outside = client.post(
            "/internal/v1/equipment-documents/render-page",
            files={"file": ("manual.pdf", content, "application/pdf")},
            data={"page_number": "99"},
        )
    assert response.status_code == 200, response.text
    assert response.json()["page"] == 2
    assert base64.b64decode(response.json()["image_base64"]).startswith(b"\x89PNG")
    assert outside.status_code == 422
