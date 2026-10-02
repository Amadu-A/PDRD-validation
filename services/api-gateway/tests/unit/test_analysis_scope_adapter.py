"""Unit-тесты закрытого обращения Gateway к user-service за областью Review."""

import json
from uuid import uuid4

import httpx
import pytest
from pdrd_api_gateway.application.ports.analysis_scope import AnalysisScopeUnavailable
from pdrd_api_gateway.infrastructure.analysis_scope import HttpUserAnalysisScopeChecker


@pytest.mark.parametrize("allowed", [True, False])
async def test_scope_adapter_uses_service_key_and_trusted_uuids(allowed: bool) -> None:
    """Клиент не подставляет browser claims и принимает только bool решения."""
    actor = uuid4()
    owner = uuid4()

    def handle(request: httpx.Request) -> httpx.Response:
        """Проверяет точный internal контракт."""
        assert request.url.path == "/internal/v1/access/review-scope"
        assert request.headers["authorization"] == "Bearer internal-secret"
        assert json.loads(request.content) == {
            "actor_user_id": str(actor),
            "owner_user_id": str(owner),
        }
        return httpx.Response(200, json={"allowed": allowed})

    checker = HttpUserAnalysisScopeChecker(
        "http://user-service:8000",
        "internal-secret",
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    assert await checker.allows(actor_user_id=actor, owner_user_id=owner) is allowed
    assert "internal-secret" not in repr(checker)


@pytest.mark.parametrize(
    ("status_code", "payload"),
    [(503, {"allowed": True}), (200, {"allowed": "true"}), (200, [True])],
)
async def test_scope_adapter_fails_closed(status_code: int, payload: object) -> None:
    """Ошибка или искажённый ответ не дают доступ к чужой задаче."""
    checker = HttpUserAnalysisScopeChecker(
        "http://user-service:8000",
        "internal-secret",
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(status_code, json=payload)
            )
        ),
    )
    with pytest.raises(AnalysisScopeUnavailable):
        await checker.allows(actor_user_id=uuid4(), owner_user_id=uuid4())
