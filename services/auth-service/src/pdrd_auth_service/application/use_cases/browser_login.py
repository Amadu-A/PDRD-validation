# services/auth-service/src/pdrd_auth_service/application/use_cases/browser_login.py

"""Единый вход браузера через AD либо подтверждённую внешнюю учётную запись."""

from typing import Protocol
from uuid import UUID

from pdrd_auth_service.application.use_cases.corporate_login import (
    VerifyCorporateLogin,
)
from pdrd_auth_service.application.use_cases.external_accounts import ExternalAccounts
from pdrd_auth_service.application.use_cases.sessions import (
    IssuedSession,
    SessionService,
)
from pdrd_auth_service.domain.identity import CorporateIdentity


class CorporateProfilePort(Protocol):
    """Связывает подтверждённый objectGUID с профилем User Service."""

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
    ) -> None:
        """Получает готовые сценарии и не создаёт инфраструктуру внутри себя."""
        self._corporate = corporate
        self._external = external
        self._profiles = profiles
        self._sessions = sessions

    async def execute(self, *, login: str, password: str) -> IssuedSession:
        """Выбирает источник только по форме логина, а роль читает User Service."""
        if not isinstance(login, str) or not isinstance(password, str):
            raise ValueError("Неверные учётные данные")
        if "@" in login:
            user_id = await self._external.authenticate(login=login, password=password)
        else:
            if self._corporate is None:
                raise CorporateLoginUnavailable("Корпоративный вход пока недоступен")
            identity = await self._corporate.execute(login=login, password=password)
            user_id = await self._profiles.provision_corporate(identity)
        return await self._sessions.issue(user_id)
