# services/admin-service/tests/functional/test_admin_concurrency.py

"""Проверяет изоляцию актёра при параллельных запросах списка пользователей."""

import asyncio
from uuid import NAMESPACE_DNS, UUID, uuid5

import httpx
import pytest
from pdrd_admin_service.application.admin_users import AdminUsers
from pdrd_admin_service.contracts.models import SessionIdentity, UserPage
from pdrd_admin_service.core.container import ApplicationContainer
from pdrd_admin_service.core.settings import Settings
from pdrd_admin_service.main import create_app

KEY = "internal-key-longer-than-thirty-two-characters"


class Sessions:
    """Превращает разные тестовые cookie в разные проверенные UUID."""

    async def introspect(self, token: str) -> SessionIdentity:
        """Даёт переключиться другим запросам перед возвратом актёра."""
        await asyncio.sleep(0)
        return SessionIdentity(
            user_id=UUID(token),
            permissions=("admin.access",),
            csrf_token="test-csrf-token-longer-than-thirty-two-characters",
        )


class Users:
    """Фиксирует каждый UUID, полученный через внутреннюю границу."""

    def __init__(self) -> None:
        """Создаёт независимый журнал теста."""
        self.seen: list[UUID] = []

    async def list_users(
        self, actor_user_id: UUID, *, limit: int, offset: int
    ) -> UserPage:
        """Сохраняет актёра после уступки управления event loop."""
        await asyncio.sleep(0)
        self.seen.append(actor_user_id)
        return UserPage(items=(), total=0, limit=limit, offset=offset)


@pytest.mark.asyncio
async def test_parallel_admin_requests_never_share_actor_context() -> None:
    """64 одновременных cookie не подменяют UUID соседнего запроса."""
    users = Users()
    settings = Settings(
        _env_file=None,
        enabled=True,
        auth_service_internal_key=KEY,
        user_service_internal_key=KEY,
    )

    async def ready() -> bool:
        """Подтверждает фиктивную готовность зависимостей."""
        return True

    async def close() -> None:
        """Закрывает фиктивный контейнер."""

    app = create_app(
        ApplicationContainer(settings, AdminUsers(Sessions(), users), ready, close)
    )
    actors = tuple(uuid5(NAMESPACE_DNS, f"admin-load-{index}") for index in range(64))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://admin-service"
    ) as client:
        responses = await asyncio.gather(
            *(
                client.get(
                    "/api/v1/admin/users?limit=10&offset=0",
                    headers={"Cookie": f"pdrd_session={actor}"},
                )
                for actor in actors
            )
        )
    assert all(response.status_code == 200 for response in responses)
    assert sorted(users.seen) == sorted(actors)
