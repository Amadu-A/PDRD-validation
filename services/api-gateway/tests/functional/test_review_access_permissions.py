# services/api-gateway/tests/functional/test_review_access_permissions.py

"""Регрессия ревью: серверные права, собственные задания и строгий Origin."""

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pdrd_api_gateway.core.identity_proxy_settings import IdentityProxySettings
from pdrd_api_gateway.domain.analysis_job import AnalysisJob
from pdrd_api_gateway.transport.http.analysis_access import enforce_analysis_job_access
from pdrd_api_gateway.transport.http.identity_authorization import (
    IdentityAuthorizer,
    enforce_identity_authorization,
)

ORIGIN = "https://pdrd.example.test"
ACTOR = uuid4()
REVIEW = [
    "review.own.read",
    "review.gold.create",
    "review.findings.decide",
    "review.approve",
    "review.pdf.download",
    "experience.capture",
]


def browser(*, granted, own=True):
    """Собирает реальные проверки прав и владельца перед прикладным обработчиком."""
    job = AnalysisJob.create(owner_user_id=ACTOR if own else uuid4())

    def private(request):
        """Имитирует актуальный ответ Auth Service без клиентских claims."""
        assert request.url.path == "/internal/v1/auth/introspect"
        return httpx.Response(
            200, json={"user_id": str(ACTOR), "permissions": REVIEW if granted else []}
        )

    settings = IdentityProxySettings(
        enabled=True,
        authorization_enabled=True,
        public_origin=ORIGIN,
        trusted_proxy_key="p" * 32,
        technical_assignment_access_key="t" * 32,
        auth_internal_key="a" * 32,
        user_service_internal_key="u" * 32,
    )
    authorizer = IdentityAuthorizer(
        settings,
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(private)
        ),
    )

    class Jobs:
        """Возвращает только задание, привязанное к выбранному владельцу."""

        async def execute(self, *, job_id):
            """Не даёт угадать другой UUID."""
            return job if job_id == job.id else None

    app = FastAPI()
    app.state.container = SimpleNamespace(
        settings=SimpleNamespace(identity_proxy=settings)
    )

    @app.middleware("http")
    async def authorize(request: Request, call_next):
        """Проверяет операцию и владельца до обработчика, как основной Gateway."""
        denial = await enforce_identity_authorization(request, authorizer)
        if denial is not None:
            return denial
        denial = await enforce_analysis_job_access(
            request, Jobs(), getattr(request.state, "identity_user_id", None)
        )
        return denial or await call_next(request)

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def execute(path: str):
        """Считает допущенную операцию выполненной без инфраструктуры."""
        return {"status": "saved"}

    client = TestClient(app)
    client.cookies.set("pdrd_session", "opaque-session")
    return client, job


@pytest.mark.parametrize(
    "operation", ["read", "gold", "edit", "decide", "approve", "pdf", "capture"]
)
@pytest.mark.parametrize(
    "granted, own, expected",
    [(False, True, 403), (True, True, 200), (True, False, 404)],
)
def test_review_grant_controls_edit_save_approve_capture_and_preserves_ownership(
    operation, granted, own, expected
):
    """Галочка открывает полное ревью собственных анализов, сохраняя запрет чужих."""
    client, job = browser(granted=granted, own=own)
    base = f"/api/v1/analyses/{job.id}"
    payload = {
        "action": {
            "gold": "add",
            "edit": "edit",
            "decide": "decide",
            "approve": "approve",
        }.get(operation),
        "finding_id": "vlm:f1",
    }
    with client:
        if operation == "read":
            response = client.get(f"{base}/review")
        elif operation == "pdf":
            response = client.get(f"{base}/reviewed-pdf")
        elif operation == "capture":
            response = client.post(
                f"/api/v1/experience/capture/{job.id}", headers={"Origin": ORIGIN}
            )
        else:
            response = client.post(
                f"{base}/review/commands", json=payload, headers={"Origin": ORIGIN}
            )
    assert response.status_code == expected


def test_review_grant_does_not_bypass_origin():
    """Даже разрешённое ревью не принимает изменения с чужого источника."""
    client, job = browser(granted=True)
    with client:
        response = client.post(
            f"/api/v1/analyses/{job.id}/review/commands",
            json={"action": "edit"},
            headers={"Origin": "https://foreign.example"},
        )
    assert response.status_code == 403
