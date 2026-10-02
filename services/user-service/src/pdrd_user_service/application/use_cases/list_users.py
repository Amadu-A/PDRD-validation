# services/user-service/src/pdrd_user_service/application/use_cases/list_users.py

"""Постраничный список пользователей только для действующего администратора."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pdrd_user_service.application.ports.repository import UnitOfWorkFactory
from pdrd_user_service.application.use_cases.users import AdminRequired
from pdrd_user_service.domain.access import Role
from pdrd_user_service.domain.identity import UserAccount
from pdrd_user_service.domain.role_assignments import effective_roles


@dataclass(frozen=True, slots=True)
class UserPage:
    """Число профилей и запрошенная страница без полей аутентификации."""

    items: tuple[UserAccount, ...]
    total: int
    limit: int
    offset: int


class AdminUserListing:
    """Проверяет платформенную роль в БД до перечисления профилей."""

    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Получает транзакционный порт и управляемый источник времени."""
        self._unit_of_work = unit_of_work
        self._clock = clock or (lambda: datetime.now(UTC))

    async def page(
        self, *, actor_user_id: UUID, limit: int = 50, offset: int = 0
    ) -> UserPage:
        """Возвращает данные лишь при действующей роли platform_admin."""
        if not isinstance(actor_user_id, UUID):
            raise TypeError("actor_user_id должен быть UUID")
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Некорректные параметры страницы")
        async with self._unit_of_work() as work:
            actor = await work.users.get_user(actor_user_id, for_update=True)
            if actor is None:
                raise AdminRequired("Администратор не найден")
            assignments = await work.users.list_assignments(
                actor_user_id, for_update=True
            )
            if Role.PLATFORM_ADMIN not in effective_roles(
                actor, assignments, (), self._clock()
            ):
                raise AdminRequired("Требуется действующая роль администратора")
            items, total = await work.users.list_users(limit=limit, offset=offset)
            return UserPage(items=items, total=total, limit=limit, offset=offset)
