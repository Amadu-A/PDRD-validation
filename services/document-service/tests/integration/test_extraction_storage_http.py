# services/document-service/tests/integration/test_extraction_storage_http.py

"""HTTP-регрессии недоступного тома: readiness и extract не обещают ложную готовность."""

from dataclasses import replace

import fitz
import pytest
from fastapi.testclient import TestClient
from pdrd_document_service.application.use_cases.enrich_document import EnrichDocument
from pdrd_document_service.core import container as composition
from pdrd_document_service.core.settings import (
    DoclingSettings,
    SpecificationServiceSettings,
)
from pdrd_document_service.infrastructure.extraction_artifacts import (
    FilesystemExtractionArtifacts,
)
from pdrd_document_service.main import create_app
from pydantic import SecretStr


def native_container():
    """Собирает локальные PDF-возможности без внешних сервисов."""
    return replace(
        composition.build_container(),
        enrich_document=None,
        enrich_specification=None,
        extraction_storage=(),
    )


def build_pdf():
    """Создаёт небольшой настоящий скан для multipart-запроса."""
    with fitz.open() as document:
        document.new_page()
        return document.tobytes()


def test_runtime_probes_the_same_stores_used_by_enabled_integrations(
    tmp_path, monkeypatch
):
    """DI проверяет обе рабочие директории, а не отдельный всегда доступный путь."""
    settings = composition.get_settings().model_copy(
        update={
            "specification_service": SpecificationServiceSettings(
                enabled=True,
                api_key=SecretStr("test-only"),
                artifacts_path=tmp_path / "spec",
            ),
            "docling": DoclingSettings(
                enabled=True,
                api_key=SecretStr("test-only"),
                artifacts_path=tmp_path / "docling",
            ),
        }
    )
    monkeypatch.setattr(composition, "get_settings", lambda: settings)
    container = composition.build_container()
    assert container.extraction_storage == (
        container.enrich_specification.extractor.artifacts,
        container.enrich_document.converter.artifacts,
    )
    assert container.enrich_specification.artifacts is container.extraction_storage[0]
    with TestClient(create_app(container)) as client:
        assert client.get("/health/ready").status_code == 200
    assert (tmp_path / "spec").is_dir() and (tmp_path / "docling").is_dir()
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.parametrize("path", ["/health/ready", "/internal/v1/pdf/extract"])
def test_unavailable_storage_is_503_before_extraction(tmp_path, monkeypatch, path):
    """Реальный отказ mkdir даёт безопасный ответ до рендеринга и внешних вызовов."""
    root = tmp_path / "private-storage-path"
    root.write_text("not-a-directory", encoding="utf-8")
    container = replace(
        native_container(), extraction_storage=(FilesystemExtractionArtifacts(root),)
    )

    def unexpected(*args, **kwargs):
        """До восстановления тома нельзя тратить ресурсы на извлечение PDF."""
        raise AssertionError("Преждевременное извлечение")

    monkeypatch.setattr(type(container.extract_pdf), "execute", unexpected)
    with TestClient(create_app(container)) as client:
        assert client.get("/health/live").status_code == 200
        response = (
            client.get(path)
            if path == "/health/ready"
            else client.post(
                path, files={"file": ("source.pdf", build_pdf(), "application/pdf")}
            )
        )
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "extraction_storage_unavailable"
    assert "private-storage-path" not in response.text
    assert root.read_text(encoding="utf-8") == "not-a-directory"


def test_late_write_failure_is_503_without_publishing_partial_ocr(
    tmp_path, monkeypatch
):
    """Потеря диска после успешной readiness не превращается в молчаливый fallback."""
    from pdrd_document_service.infrastructure import extraction_artifacts

    calls = []
    store = FilesystemExtractionArtifacts(tmp_path)

    class Converter:
        """Моделирует успешный внешний OCR перед отказом долговечной записи."""

        async def convert(self, **kwargs):
            """Не возвращает несохранённое чтение как успешное."""
            calls.append(kwargs["page_number"])
            await store.save("a" * 64, {"result": "OCR"})
            raise AssertionError("Нельзя публиковать несохранённый результат")

    def denied(*args, **kwargs):
        """Воспроизводит errno EACCES внутри атомарного сохранения."""
        raise PermissionError(13, "test-write-denied")

    monkeypatch.setattr(extraction_artifacts.tempfile, "mkstemp", denied)
    with fitz.open() as document:
        document.new_page()
        content = document.tobytes()
    container = replace(
        native_container(),
        extraction_storage=(store,),
        enrich_document=EnrichDocument(Converter()),
    )
    with TestClient(create_app(container)) as client:
        assert client.get("/health/ready").status_code == 200
        response = client.post(
            "/internal/v1/pdf/extract",
            files={"file": ("scan.pdf", content, "application/pdf")},
        )
    assert calls == [1]
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "extraction_storage_unavailable"
    assert not list(tmp_path.iterdir())
