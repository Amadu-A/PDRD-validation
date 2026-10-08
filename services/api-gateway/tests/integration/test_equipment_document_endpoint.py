# services/api-gateway/tests/integration/test_equipment_document_endpoint.py

"""Проверяет выдачу только привязанного к анализу сохранённого EQ snapshot."""

from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from pdrd_api_gateway.application.use_cases.check_readiness import CheckReadiness
from pdrd_api_gateway.core.container import ApplicationContainer
from pdrd_api_gateway.core.settings import (
    DatabaseSettings,
    EquipmentSearchSettings,
    Settings,
)
from pdrd_api_gateway.domain.analysis_job import AnalysisJob
from pdrd_api_gateway.infrastructure.equipment_search import EquipmentSearchClient
from pdrd_api_gateway.main import create_app

SOURCE = "EQ-" + "a" * 32


class StaticReadiness:
    """Имитирует готовность инфраструктуры."""

    async def is_ready(self) -> bool:
        """Возвращает готовность."""
        return True


class JobReader:
    """Возвращает одно известное задание."""

    def __init__(self, job: AnalysisJob) -> None:
        """Сохраняет задание."""
        self.job = job

    async def execute(self, *, job_id: UUID) -> AnalysisJob | None:
        """Читает задание по ID."""
        return self.job if self.job.id == job_id else None


async def noop_shutdown() -> None:
    """Имитирует освобождение ресурсов."""


def _client(job: AnalysisJob) -> TestClient:
    """Создаёт Gateway с тестовым чтением задания."""
    settings = Settings(
        _env_file=None,
        environment="test",
        database=DatabaseSettings(password="test-password"),
        equipment_search=EquipmentSearchSettings(
            enabled=True,
            internal_key="test-key",
        ),
    )
    shutdown: Callable[[], Awaitable[None]] = noop_shutdown
    container = ApplicationContainer(
        settings=settings,
        check_readiness=CheckReadiness(
            database=StaticReadiness(),
            broker=StaticReadiness(),
        ),
        shutdown_callback=shutdown,
        get_analysis_job=JobReader(job),  # type: ignore[arg-type]
    )
    return TestClient(create_app(container=container))


def test_only_assigned_equipment_snapshot_is_downloaded(monkeypatch) -> None:
    """Чужой source ID не передаётся во внутренний сервис."""
    job = AnalysisJob.create(document_id=uuid4())
    downloaded: list[str] = []

    async def fake_state(self, document_id):
        return {"results": [{"document": {"source_id": SOURCE}}]}

    async def fake_document(self, source_id):
        downloaded.append(source_id)
        return b"%PDF-1.4 fixture", "application/pdf"

    monkeypatch.setattr(EquipmentSearchClient, "state", fake_state)
    monkeypatch.setattr(EquipmentSearchClient, "document", fake_document)
    with _client(job) as client:
        denied = client.get(
            f"/api/v1/analyses/{job.id}/equipment-documents/EQ-other",
        )
        allowed = client.get(
            f"/api/v1/analyses/{job.id}/equipment-documents/{SOURCE}",
        )

    assert denied.status_code == 404
    assert allowed.status_code == 200
    assert allowed.content == b"%PDF-1.4 fixture"
    assert allowed.headers["content-disposition"].startswith("attachment;")
    assert downloaded == [SOURCE]


def test_html_snapshot_is_sandboxed(monkeypatch) -> None:
    """HTML производителя скачивается без исполнения в origin PDRD."""
    job = AnalysisJob.create(document_id=uuid4())

    async def fake_state(self, document_id):
        return {"results": [{"document": {"source_id": SOURCE}}]}

    async def fake_document(self, source_id):
        return b"<html>manufacturer</html>", "text/html"

    monkeypatch.setattr(EquipmentSearchClient, "state", fake_state)
    monkeypatch.setattr(EquipmentSearchClient, "document", fake_document)
    with _client(job) as client:
        response = client.get(
            f"/api/v1/analyses/{job.id}/equipment-documents/{SOURCE}",
        )

    assert response.status_code == 200
    assert response.headers["content-security-policy"] == "sandbox"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].endswith(".html")
