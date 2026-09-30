# services/user-service/src/pdrd_user_service/transport/http/dependencies.py

"""Зависимости HTTP API и проверка единственного сервисного секрета."""

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from pdrd_user_service.core.container import ApplicationContainer


def get_container(request: Request) -> ApplicationContainer:
    """Возвращает контейнер текущего экземпляра FastAPI."""
    return request.app.state.container


Container = Annotated[ApplicationContainer, Depends(get_container)]


def require_service_key(request: Request, container: Container) -> None:
    """Отклоняет запрос без точного служебного Bearer ключа, включая пустой ключ."""
    expected = container.settings.internal_key.get_secret_value()
    received = request.headers.get("authorization", "")
    if not expected or not secrets.compare_digest(
        received.encode(), f"Bearer {expected}".encode()
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется сервисная авторизация.",
            headers={"WWW-Authenticate": "Bearer"},
        )
