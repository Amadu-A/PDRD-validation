# services/api-gateway/tests/functional/test_normative_atomic_access.py

"""Права, сессия, CSRF и область нормативного документа для новых операций каталога."""

from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pdrd_api_gateway.transport.http.routers.normative_requirements import router

from .test_normative_section_permissions import ACTOR, ORIGIN, SECTION, build_app


def proof():
    """Тестовая аттестация не утверждает статус реального нормативного документа."""
    return {
        "document_code": "СП 99.13330.2026",
        "edition": "2026",
        "edition_status": "current",
        "requirement_binding": "mandatory",
        "clause_scope": ["5.1"],
        "applicability_scope": "Насосы",
        "source_url": "https://example.org/status",
        "source_quote": "Стендовое подтверждение.",
        "confirmed_on": "2026-01-01",
        "valid_from": "2026-01-01",
        "valid_until": "2026-12-31",
    }


def application(role, *, assigned=True):
    """Подключает новые маршруты к настоящей проверке Auth/User Service из существующих тестов."""
    app, catalog, _ = build_app(role, allowed_sections=(SECTION,) if assigned else ())

    async def get_document(**kwargs):
        """Ресурс относится к конкретному разделу, доступ проверяет production dependency."""
        return SimpleNamespace(section_id=SECTION)

    async def record(**kwargs):
        """Показывает серверного автора, дошедшего до прикладного слоя."""
        catalog.calls.append(kwargs)
        return {"verified_by": str(kwargs["actor_id"])}

    async def queue(**kwargs):
        """Не вызывается при отказе прав, Origin или доступа к разделу."""
        catalog.calls.append(kwargs)
        return {"state": "queued"}

    catalog.get_document = get_document
    catalog.record_status_evidence = record
    catalog.queue_requirements = queue
    app.include_router(router)
    return app, catalog


@pytest.mark.parametrize("role", ["guest", "designer", "head", "admin"])
@pytest.mark.parametrize("operation", ["requirements/index", "status-evidence"])
def test_atomic_mutations_require_real_permission_and_ignore_forged_actor(
    role, operation
):
    """Заголовок браузера не заменяет cookie и не позволяет аттестовать статус от другого лица."""
    app, catalog = application(role)
    with TestClient(app) as client:
        if role != "guest":
            client.cookies.set("pdrd_session", "verified-session")
        result = client.post(
            f"/api/v1/normative/documents/{UUID(int=3)}/{operation}",
            headers={"Origin": ORIGIN, "X-PDRD-Actor-ID": str(UUID(int=99))},
            json=proof() if operation == "status-evidence" else None,
        )
    allowed = role in {"head", "admin"}
    assert result.status_code == (
        (201 if operation == "status-evidence" else 202)
        if allowed
        else 401
        if role == "guest"
        else 403
    )
    assert bool(catalog.calls) is allowed
    if allowed and operation == "status-evidence":
        assert result.json()["verified_by"] == str(ACTOR)


@pytest.mark.parametrize("assigned", [True, False])
def test_atomic_status_respects_origin_and_assigned_section(assigned):
    """Наличие нормативного права не снимает CSRF и ограничение раздела документа."""
    app, catalog = application("head", assigned=assigned)
    with TestClient(app) as client:
        client.cookies.set("pdrd_session", "verified-session")
        for origin in ("https://attacker.test", ORIGIN):
            result = client.post(
                f"/api/v1/normative/documents/{UUID(int=3)}/status-evidence",
                headers={"Origin": origin},
                json=proof(),
            )
            assert result.status_code == (201 if assigned and origin == ORIGIN else 403)
    assert bool(catalog.calls) is assigned
