# services/admin-service/src/pdrd_admin_service/application/admin_users.py

"""Проверяет действующую сессию и разрешает административные операции."""

from uuid import UUID

from pdrd_admin_service.application.errors import (
    AuthenticationRequired,
    CsrfRejected,
    InvalidRoleRequest,
    PermissionDenied,
    RoleConflict,
    TargetNotFound,
    UpstreamUnavailable,
)
from pdrd_admin_service.application.ports import SessionVerifier, UserDirectory
from pdrd_admin_service.application.session_authorization import authorize_session
from pdrd_admin_service.contracts.models import (
    ReplaceRoleRequest,
    RoleDetailResponse,
    SessionIdentity,
    UserPage,
    UserResponse,
)

__all__ = (
    "AdminUsers",
    "AuthenticationRequired",
    "CsrfRejected",
    "InvalidRoleRequest",
    "PermissionDenied",
    "RoleConflict",
    "TargetNotFound",
    "UpstreamUnavailable",
)


class AdminUsers:
    """Сохраняет права в user-service и не открывает доступ к его таблицам."""

    def __init__(self, sessions: SessionVerifier, users: UserDirectory) -> None:
        """Принимает только порты доверенных сервисов."""
        self._sessions = sessions
        self._users = users

    async def _authorize(
        self, token: str | None, required_permission: str, csrf: str | None = None
    ) -> SessionIdentity:
        """Проверяет cookie на каждом запросе и CSRF перед изменением данных."""
        return await authorize_session(
            self._sessions, token, required_permission, csrf=csrf
        )

    async def list_users(
        self, token: str | None, *, limit: int, offset: int
    ) -> UserPage:
        """Открывает каталог лишь действующему администратору."""
        identity = await self._authorize(token, "admin.access")
        return await self._users.list_users(
            identity.user_id, limit=limit, offset=offset
        )

    async def get_user(self, token: str | None, user_id: UUID) -> UserResponse:
        """Читает профиль через маршрут с повторной проверкой актёра в БД."""
        identity = await self._authorize(token, "admin.access")
        details = await self._users.get_roles(identity.user_id, user_id)
        return details.user

    async def get_roles(self, token: str | None, user_id: UUID) -> RoleDetailResponse:
        """Читает действующие назначения только для администратора."""
        identity = await self._authorize(token, "admin.access")
        return await self._users.get_roles(identity.user_id, user_id)

    async def replace_role(
        self,
        token: str | None,
        target_user_id: UUID,
        command: ReplaceRoleRequest,
        *,
        csrf: str | None,
    ) -> RoleDetailResponse:
        """Атомарно меняет рабочую роль с CSRF и сравнением версии в БД."""
        identity = await self._authorize(token, "users.roles.assign", csrf or "")
        return await self._users.replace_role(identity.user_id, target_user_id, command)
