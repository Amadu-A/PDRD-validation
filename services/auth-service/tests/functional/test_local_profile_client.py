# services/auth-service/tests/functional/test_local_profile_client.py

"""Auth Service передаёт User Service только устойчивый ключ и открытое имя."""

import json
from uuid import uuid4

import httpx
import pytest
from pdrd_auth_service.infrastructure.user_service import UserServiceClient


@pytest.mark.asyncio
async def test_bootstrap_client_never_transmits_password():
    """Контракт bootstrap не содержит хеша/пароля и сохраняет служебный ключ."""
    user_id, subject = uuid4(), uuid4()
    requests = []

    def handle(request):
        """Фиксирует разрешённое тело и возвращает профиль локального аккаунта."""
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "user_id": str(user_id),
                "kind": "local",
                "tier": "member",
                "status": "active",
                "display_name": "admin",
                "login": "admin",
                "authorization_version": 2,
            },
        )

    async with httpx.AsyncClient(
        base_url="http://user-service", transport=httpx.MockTransport(handle)
    ) as http:
        client = UserServiceClient(
            "http://user-service", "internal-test-key", 5, client=http
        )
        assert (
            await client.provision_local_superuser(subject=subject, username="admin")
            == user_id
        )
        assert json.loads(requests[0].content) == {
            "subject": str(subject),
            "username": "admin",
        }
        assert requests[0].headers["authorization"] == "Bearer internal-test-key"
        assert (await client.find_by_login("admin")).kind == "local"


@pytest.mark.asyncio
async def test_unknown_login_and_existing_bootstrap_have_different_internal_results():
    """404 допускает проверку AD; 409 делает CLI повторного bootstrap понятным."""

    def handle(request):
        """Подменяет отсутствие логина либо долговечный bootstrap guard."""
        return httpx.Response(404 if request.url.path.endswith("lookup-login") else 409)

    async with httpx.AsyncClient(
        base_url="http://user-service", transport=httpx.MockTransport(handle)
    ) as http:
        client = UserServiceClient("http://user-service", "key", 5, client=http)
        assert await client.find_by_login("unknown") is None
        with pytest.raises(ValueError, match="администратор"):
            await client.provision_local_superuser(subject=uuid4(), username="admin")
