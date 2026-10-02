# services/auth-service/src/pdrd_auth_service/core/runtime.py

"""Composition root HTTP Auth Service без побочных соединений при импорте."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from pdrd_auth_service.application.use_cases.browser_login import BrowserLogin
from pdrd_auth_service.application.use_cases.current_user import ResolveCurrentUser
from pdrd_auth_service.application.use_cases.external_accounts import ExternalAccounts
from pdrd_auth_service.application.use_cases.sessions import SessionService
from pdrd_auth_service.core.container import build_corporate_login, build_session_policy
from pdrd_auth_service.core.settings import Settings
from pdrd_auth_service.infrastructure.database.engine import (
    build_async_engine,
    build_session_factory,
)
from pdrd_auth_service.infrastructure.database.external_credentials import (
    SqlAlchemyExternalCredentialStore,
)
from pdrd_auth_service.infrastructure.database.rate_limits import (
    SqlAlchemyAttemptLimiter,
)
from pdrd_auth_service.infrastructure.database.repository import SqlAlchemySessionStore
from pdrd_auth_service.infrastructure.smtp import (
    SmtpVerificationEmail,
    VerificationDeliveryUnavailable,
)
from pdrd_auth_service.infrastructure.user_service import UserServiceClient


class DisabledEmail:
    """Явно закрывает регистрацию до настройки защищённого SMTP."""

    async def send_verification(self, *, recipient: str, token: str) -> None:
        """Не отправляет письмо при выключенной функции."""
        del recipient, token
        raise VerificationDeliveryUnavailable("Подтверждение email не настроено")


@dataclass(slots=True)
class AuthRuntime:
    """Передаёт только готовые зависимости маршрутам и владеет пулами."""

    settings: Settings
    engine: AsyncEngine
    users: UserServiceClient
    sessions: SessionService
    current: ResolveCurrentUser
    external: ExternalAccounts
    login: BrowserLogin
    limiter: SqlAlchemyAttemptLimiter

    async def close(self) -> None:
        """Закрывает независимые пулы БД и User Service."""
        await self.users.close()
        await self.engine.dispose()


def build_runtime(settings: Settings) -> AuthRuntime:
    """Создаёт HTTP-сценарии только после проверки конфигурации запуска."""
    if not settings.http.enabled:
        raise RuntimeError("HTTP Auth Service выключен")
    engine = build_async_engine(settings.database)
    session_factory = build_session_factory(engine)
    users = UserServiceClient(
        settings.http.user_service_url,
        settings.http.user_service_internal_key.get_secret_value(),
        settings.http.request_timeout_seconds,
    )
    sessions = SessionService(
        SqlAlchemySessionStore(session_factory),
        users,
        policy=build_session_policy(settings),
    )
    external = ExternalAccounts(
        SqlAlchemyExternalCredentialStore(session_factory),
        users,
        SmtpVerificationEmail(settings.email)
        if settings.email.enabled
        else DisabledEmail(),
    )
    corporate = build_corporate_login(settings) if settings.enabled else None
    return AuthRuntime(
        settings=settings,
        engine=engine,
        users=users,
        sessions=sessions,
        current=ResolveCurrentUser(sessions, users),
        external=external,
        login=BrowserLogin(corporate, external, users, sessions),
        limiter=SqlAlchemyAttemptLimiter(
            session_factory, settings.http.csrf_key.get_secret_value()
        ),
    )
