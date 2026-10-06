# services/document-service/tests/integration/test_pdf_selection_http.py

"""Реальный PDF: 200 страниц допустимы, 201 даёт понятный отказ без рендеринга."""

from dataclasses import replace

import fitz
from fastapi.testclient import TestClient
from pdrd_document_service.application.use_cases.extract import ExtractPdfDocument
from pdrd_document_service.core.container import build_container
from pdrd_document_service.core.settings import PdfSettings
from pdrd_document_service.infrastructure.pdf.pymupdf import PyMuPdfReader
from pdrd_document_service.main import create_app


def pdf(pages):
    """Генерирует небольшой многостраничный PDF в памяти."""
    with fitz.open() as document:
        for _ in range(pages):
            document.new_page()
        return document.tobytes()


def browser(monkeypatch):
    """Изменяет только тестовые настройки и запрещает создание изображений."""

    def no_render(*args, **kwargs):
        """Проверка выбора не должна рендерить страницы."""
        raise AssertionError("Рендеринг при предварительной проверке")

    monkeypatch.setattr(PyMuPdfReader, "extract", no_render)
    container = build_container()
    settings = container.settings.model_copy(
        update={"pdf": PdfSettings(max_analysis_pages=200)}
    )
    container = replace(
        container,
        settings=settings,
        extract_pdf=ExtractPdfDocument(
            PyMuPdfReader(render_max_side=500, text_limit=100),
            settings.pdf.max_upload_bytes,
            200,
        ),
    )
    return TestClient(create_app(container=container))


def test_two_hundred_pages_are_accepted_without_rendering(monkeypatch):
    """Точная граница 200 проходит через настоящий PDF-reader и HTTP."""
    response = browser(monkeypatch).post(
        "/internal/v1/pdf/inspect",
        files={"file": ("ВК.pdf", pdf(200), "application/pdf")},
    )
    assert response.status_code == 200
    assert response.json()["selected_pages"] == list(range(1, 201))
    assert response.json()["max_analysis_pages"] == 200


def test_larger_pdf_has_friendly_error_and_can_select_two_hundred_pages(monkeypatch):
    """Большой исходный PDF можно проверить несколькими выбранными диапазонами."""
    content = pdf(201)
    client = browser(monkeypatch)
    response = client.post(
        "/internal/v1/pdf/inspect",
        files={"file": ("ВК.pdf", content, "application/pdf")},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "201" in detail and "200" in detail and "Страницы PDF" in detail
    selected = client.post(
        "/internal/v1/pdf/inspect",
        files={"file": ("ВК.pdf", content, "application/pdf")},
        data={"pages": "1-200"},
    )
    assert selected.status_code == 200 and selected.json()["total_pages"] == 201
    # Вторая защита внутри extraction/workflow использует ту же проверку и тот же текст.
    extraction = client.post(
        "/internal/v1/pdf/extract",
        files={"file": ("ВК.pdf", content, "application/pdf")},
    )
    assert extraction.status_code == 422 and extraction.json()["detail"] == detail
