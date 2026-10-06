# services/auth-service/src/pdrd_auth_service/application/use_cases/current_user.py

"""Согласованное разрешение сессии, профиля и актуальных полномочий."""

from dataclasses import dataclass

from pdrd_auth_service.application.ports.profiles import (
    PermissionSnapshot,
    ProfilePort,
    ProfileSnapshot,
)
from pdrd_auth_service.application.use_cases.sessions import (
    SessionInvalid,
    SessionService,
    SessionView,
)


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """Снимок для доверенного ответа HTTP без токена и его хеша."""

    session: SessionView
    profile: ProfileSnapshot
    permissions: PermissionSnapshot


class ResolveCurrentUser:
    """Не допускает использования устаревших ролей после изменения версии."""

    def __init__(self, sessions: SessionService, profiles: ProfilePort) -> None:
        """Получает только абстракции сессии и каталога."""
        self._sessions = sessions
        self._profiles = profiles

    async def execute(self, token: str) -> CurrentUser:
        """Проверяет обе версии и статус перед выдачей роли браузеру."""
        session = await self._sessions.resolve(token)
        profile = await self._profiles.get_user(session.user_id)
        permissions = await self._profiles.get_permissions(session.user_id)
        if (
            profile is None
            or profile.user_id != session.user_id
            or permissions.user_id != session.user_id
            or profile.status != "active"
            or permissions.status != "active"
            or profile.authorization_version != session.authorization_version
            or permissions.authorization_version != session.authorization_version
            or profile.tier != permissions.tier
        ):
            raise SessionInvalid("Сессия недействительна")
        return CurrentUser(session, profile, permissions)
