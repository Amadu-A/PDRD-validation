# services/auth-service/src/pdrd_auth_service/core/container.py

"""Собирает прикладной сценарий входа из настроек и LDAPS-адаптера.

Пока отсутствует публичный HTTP-вход, эта фабрика не запускает процесс и не
подключается к AD. Отключённый сервис не выдаёт готовый сценарий проверки.
"""

from datetime import timedelta
from pathlib import Path

from pdrd_auth_service.application.use_cases.corporate_login import (
    VerifyCorporateLogin,
)
from pdrd_auth_service.core.settings import Settings
from pdrd_auth_service.domain.session import SessionPolicy
from pdrd_auth_service.infrastructure.ldaps import (
    LdapsConnectionConfig,
    LdapsCredentialVerifier,
)


def build_corporate_login(settings: Settings) -> VerifyCorporateLogin:
    """Создаёт проверку корпоративного входа только с активным доверенным TLS."""
    if not settings.enabled:
        raise RuntimeError("Auth Service выключен")
    ad = settings.ad
    config = LdapsConnectionConfig(
        host=ad.controller_host,
        port=ad.port,
        base_dn=ad.base_dn,
        domain=ad.domain,
        ca_bundle_path=Path(ad.ca_bundle_path),
        connect_timeout_seconds=ad.connect_timeout_seconds,
        receive_timeout_seconds=ad.receive_timeout_seconds,
    )
    return VerifyCorporateLogin(LdapsCredentialVerifier(config))


def build_session_policy(settings: Settings) -> SessionPolicy:
    """Преобразует проверенные значения env в серверные пределы сессии."""
    return SessionPolicy(
        idle_timeout=timedelta(seconds=settings.sessions.idle_timeout_seconds),
        absolute_timeout=timedelta(seconds=settings.sessions.absolute_timeout_seconds),
    )
