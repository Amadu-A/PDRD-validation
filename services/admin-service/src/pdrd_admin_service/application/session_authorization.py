# services/admin-service/src/pdrd_admin_service/application/session_authorization.py

"""Общая проверка браузерной сессии и CSRF для административных сценариев."""

import secrets

from pdrd_admin_service.application.errors import (
    AuthenticationRequired,
    CsrfRejected,
    PermissionDenied,
)
from pdrd_admin_service.application.ports import SessionVerifier
from pdrd_admin_service.contracts.models import SessionIdentity


async def authorize_session(
    sessions: SessionVerifier,
    token: str | None,
    required_permission: str,
    *,
    csrf: str | None = None,
) -> SessionIdentity:
    """На каждом запросе сверяет cookie, право и при записи CSRF токен."""
    if not token:
        raise AuthenticationRequired
    identity = await sessions.introspect(token)
    if required_permission not in identity.permissions:
        raise PermissionDenied
    if csrf is not None and not secrets.compare_digest(csrf, identity.csrf_token):
        raise CsrfRejected
    return identity
