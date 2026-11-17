# services/document-service/tests/integration/test_pdf_annotation_http.py

"""Регрессии полного multipart-отчёта и безопасной диагностики генерации PDF."""

import json
from dataclasses import replace

import fitz
import pytest
from fastapi.testclient import TestClient
from pdrd_document_service.application.ports.pdf_annotation import (
    PdfAnnotationGenerationError,
)
from pdrd_document_service.core.container import build_container
from pdrd_document_service.main import create_app


def source_pdf():
    """Создаёт исходную страницу без персональных данных."""
    with fitz.open() as document:
        document.new_page().insert_text((72, 72), "Original")
        return document.tobytes()


def payload():
    """Возвращает минимальный полный контракт экспорта."""
    return {
        "annotations": [],
        "report": {
            "title": "Отчёт",
            "metadata": [],
            "summary": "",
            "findings": [],
            "limitations": [],
        },
    }


@pytest.mark.parametrize("as_file", [False, True])
def test_small_legacy_and_file_payload(as_file):
    """Старые небольшие поля и новый JSON-файл формируют настоящий PDF."""
    files = {"file": ("source.pdf", source_pdf(), "application/pdf")}
    raw = json.dumps(payload(), ensure_ascii=False).encode()
    if as_file:
        files["payload"] = ("annotations.json", raw, "application/json")
    with TestClient(create_app(container=build_container())) as client:
        response = client.post(
            "/internal/v1/pdf/annotate",
            files=files,
            data={} if as_file else {"payload": raw.decode()},
            headers={"X-Request-ID": "export-test"},
        )
    assert response.status_code == 200, response.text
    assert response.headers["x-request-id"] == "export-test"
    with fitz.open(stream=response.content) as document:
        assert len(document) == 2
        assert "Original" in document[0].get_text()


def test_full_report_over_one_megabyte_is_not_truncated():
    """Граница 1 МБ multipart не теряет ни одну из записей полного отчёта."""
    data = payload()
    # Большой идентификатор в метаданных проверяет транспорт без тысяч страниц текста.
    # Полное содержимое доходит до writer; реальный рендер проверяется выше.
    data["report"]["summary"] = "доказательство" * 90000
    raw = json.dumps(data, ensure_ascii=False).encode()
    assert len(raw) > 1024 * 1024
    captured = []

    class Writer:
        """Проверяет сохранность большого текстового поля на границе HTTP."""

        def build(self, *, content, annotations, report):
            """Сохраняет полученный отчёт, не расходуя время на его вёрстку."""
            captured.append(report.summary)
            return content

    container = build_container()
    container = replace(
        container,
        build_annotated_pdf=replace(container.build_annotated_pdf, writer=Writer()),
    )
    with TestClient(create_app(container=container)) as client:
        response = client.post(
            "/internal/v1/pdf/annotate",
            files={
                "file": ("source.pdf", source_pdf(), "application/pdf"),
                "payload": ("annotations.json", raw, "application/json"),
            },
        )
    assert response.status_code == 200, response.text
    assert captured == [data["report"]["summary"]]


def test_generation_failure_has_safe_stage_and_correlated_server_stack(caplog):
    """Причина не теряется; текст PDF и исключения не попадает в публичный ответ/лог."""

    class Writer:
        """Воспроизводит внутренний сбой библиотеки PDF."""

        def build(self, **kwargs):
            """Возвращает ту же цепочку исключений, что настоящий адаптер."""
            try:
                raise ValueError("SECRET-DOCUMENT-TEXT")
            except ValueError as error:
                raise PdfAnnotationGenerationError(
                    operation="extend_page_boxes", page_number=3
                ) from error

    container = build_container()
    container = replace(
        container,
        build_annotated_pdf=replace(container.build_annotated_pdf, writer=Writer()),
    )
    with TestClient(create_app(container=container)) as client:
        response = client.post(
            "/internal/v1/pdf/annotate",
            files={
                "file": ("source.pdf", source_pdf(), "application/pdf"),
                "payload": (
                    "annotations.json",
                    json.dumps(payload()),
                    "application/json",
                ),
            },
            headers={"X-Request-ID": "export-failure"},
        )
    assert response.status_code == 500
    assert response.json()["detail"]["stage"] == "extend_page_boxes"
    assert response.json()["detail"]["request_id"] == "export-failure"
    assert "error_type=ValueError" in caplog.text
    assert "SECRET-DOCUMENT-TEXT" not in response.text
    # traceback.format_tb включает строку исходного кода; текст исключения может
    # оказаться литералом теста. В рабочем коде стек тоже не должен печатать строки.
    assert "SECRET-DOCUMENT-TEXT" not in caplog.text
