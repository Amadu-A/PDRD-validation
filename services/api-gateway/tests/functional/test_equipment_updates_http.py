# services/api-gateway/tests/functional/test_equipment_updates_http.py

"""HTTP дополнения принимает только версии и соблюдает закрытый канал Review."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pdrd_api_gateway.core.settings import Settings
from pdrd_api_gateway.transport.http.dependencies import get_container
from pdrd_api_gateway.transport.http.identity_authorization import required_permission
from pdrd_api_gateway.transport.http.routers import equipment_search


def test_public_import_cannot_supply_findings_or_bypass_review_channel(monkeypatch):
    """Нельзя передать чужие оригиналы, actor, либо команду с другого сайта."""
    job_id = uuid4()
    settings = Settings(
        _env_file=None,
        review={
            "enabled": True,
            "controlled_access": True,
            "actor": "engineer:test",
            "ui_key": "u" * 40,
            "internal_key": "i" * 40,
        },
    )
    managed = AsyncMock(return_value={"job_id": str(job_id), "revision": 6})
    container = SimpleNamespace(
        settings=settings, manage_review=SimpleNamespace(execute=managed)
    )
    proposal = {
        "revision": 20,
        "findings": [{"finding_id": "EQ-server", "comment": "Серверное замечание"}],
    }
    builder = SimpleNamespace(execute=AsyncMock(return_value=proposal))
    monkeypatch.setattr(
        equipment_search, "build_equipment_update", lambda container: builder
    )
    app = FastAPI()
    app.state.identity_authorizer = None
    app.include_router(equipment_search.router)
    app.dependency_overrides[get_container] = lambda: container
    endpoint = f"/api/v1/analyses/{job_id}/review/equipment-updates"
    command = {"revision": 20, "expected_revision": 5}
    headers = {"X-PDRD-Review-Key": "u" * 40, "Origin": "http://testserver"}
    with TestClient(app) as client:
        assert client.post(endpoint, json=command).status_code == 403
        assert (
            client.post(
                endpoint,
                json=command,
                headers={**headers, "Origin": "https://other.example"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                endpoint,
                json={**command, "findings": [{"finding_id": "forged"}]},
                headers=headers,
            ).status_code
            == 422
        )
        managed.assert_not_awaited()
        response = client.post(endpoint, json=command, headers=headers)
    assert response.status_code == 200
    builder.execute.assert_awaited_once_with(job_id=job_id, revision=20)
    assert managed.await_args.kwargs["operation"] == "equipment_update"
    assert managed.await_args.kwargs["command"]["findings"] == proposal["findings"]
    assert required_permission("POST", endpoint) == ("review.findings.decide",)
