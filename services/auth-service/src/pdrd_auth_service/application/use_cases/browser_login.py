# services/auth-service/src/pdrd_auth_service/application/use_cases/browser_login.py

"""Единый вход браузера через локальные учётные данные, AD или email."""

from typing import Protocol
from uuid import UUID

from pdrd_auth_service.application.ports.profiles import ProfileSnapshot
from pdrd_auth_service.application.use_cases.corporate_login import (
    VerifyCorporateLogin,
)
from pdrd_auth_service.application.use_cases.external_accounts import ExternalAccounts
from pdrd_auth_service.application.use_cases.local_accounts import LocalAccounts
from pdrd_auth_service.application.use_cases.sessions import (
    IssuedSession,
    SessionService,
)
from pdrd_auth_service.core.observability import log_execution_time
from pdrd_auth_service.domain.identity import CorporateIdentity


class CorporateProfilePort(Protocol):
    """Связывает подтверждённый objectGUID с профилем User Service."""

    async def find_by_login(self, login: str) -> ProfileSnapshot | None:
        """Возвращает источник уже сохранённого профиля без пароля."""
        ...

    async def provision_corporate(self, identity: CorporateIdentity) -> UUID:
        """Возвращает внутренний UUID идемпотентного профиля."""
        ...


class CorporateLoginUnavailable(RuntimeError):
    """LDAPS вход выключен до подтверждения доверенного CA и сети."""


class BrowserLogin:
    """Выдаёт сессию лишь после доказательства личности и статуса профиля."""

    def __init__(
        self,
        corporate: VerifyCorporateLogin | None,
        external: ExternalAccounts,
        profiles: CorporateProfilePort,
        sessions: SessionService,
        local: LocalAccounts,
    ) -> None:
        """Получает готовые сценарии и не создаёт инфраструктуру внутри себя."""
        self._corporate = corporate
        self._external = external
        self._profiles = profiles
        self._sessions = sessions
        self._local = local

    @log_execution_time(operation="browser_login")
    async def execute(self, *, login: str, password: str) -> IssuedSession:
        """Сначала читает каталог; локальный пароль и AD проверяет раздельно."""
        if not isinstance(login, str) or not isinstance(password, str):
            raise ValueError("Неверные учётные данные")
        profile = await self._profiles.find_by_login(login)
        if profile is not None and profile.status != "active":
            raise PermissionError("Неверные учётные данные")
        if profile is not None and profile.kind == "local":
            user_id = await self._local.authenticate(username=login, password=password)
            if user_id != profile.user_id:
                raise PermissionError("Неверные учётные данные")
        elif (profile is not None and profile.kind == "external") or (
            profile is None and "@" in login
        ):
            user_id = await self._external.authenticate(login=login, password=password)
        else:
            if self._corporate is None:
                raise CorporateLoginUnavailable("Корпоративный вход пока недоступен")
            identity = await self._corporate.execute(login=login, password=password)
            user_id = await self._profiles.provision_corporate(identity)
        return await self._sessions.issue(user_id)
