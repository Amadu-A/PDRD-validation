# services/api-gateway/tests/integration/test_equipment_sources_permissions.py

"""Делегированное право открывает EQ-каталог без выдачи административной роли."""

from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

import pytest
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
from pdrd_api_gateway.transport.http.identity_authorization import VerifiedIdentity


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


def _client(job: AnalysisJob, identity) -> TestClient:
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
    app = create_app(container=container)

    @app.middleware("http")
    async def attach_identity(request, call_next):
        """Подставляет только доверенный серверный контекст тестовой сессии."""
        request.state.verified_identity = identity
        return await call_next(request)

    return TestClient(app)


@pytest.mark.parametrize(
    "permissions",
    [frozenset(), frozenset({"admin.access"}), frozenset({"equipment_sources.manage"})],
)
def test_equipment_catalog_requires_live_permission(monkeypatch, permissions):
    """Разрешение EQ достаточно; одно admin.access не подменяет нужное право."""
    user_id = uuid4()
    identity = VerifiedIdentity(user_id, permissions)
    calls = []

    async def sources(self, **kwargs):
        """Отдаёт тестовый каталог без внешней сети."""
        calls.append("sources")
        return [{"manufacturer": "CHINT", "hostname": "chint.ru", "status": "trusted"}]

    async def update(self, payload, actor):
        """Фиксирует инициатора из проверенного контекста Gateway."""
        calls.append(actor)
        return payload

    monkeypatch.setattr(EquipmentSearchClient, "sources", sources)
    monkeypatch.setattr(EquipmentSearchClient, "update_source", update)
    with _client(AnalysisJob.create(document_id=uuid4()), identity) as client:
        response = client.get("/api/v1/equipment-sources")
        changed = client.put(
            "/api/v1/equipment-sources",
            json={
                "manufacturer": "CHINT",
                "hostname": "chint.ru",
                "status": "trusted",
                "reason": "Подтверждение инженером",
            },
        )
    allowed = "equipment_sources.manage" in permissions
    assert response.status_code == (200 if allowed else 403)
    assert changed.status_code == (200 if allowed else 403)
    assert calls == (["sources", user_id] if allowed else [])
