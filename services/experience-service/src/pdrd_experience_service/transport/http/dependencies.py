# services/experience-service/src/pdrd_experience_service/transport/http/dependencies.py

"""Получение зависимостей HTTP-запросов Experience Service.

Контейнер создаётся при инициализации FastAPI-приложения
и сохраняется в application.state.

HTTP endpoints не создают SQLAlchemy engine, репозитории
или другие инфраструктурные компоненты самостоятельно.
"""

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from pdrd_experience_service.core.container import (
    ApplicationContainer,
)


def get_container(
    request: Request,
) -> ApplicationContainer:
    """Возвращает общий контейнер текущего приложения."""
    return request.app.state.container


def trusted_actor(
    request: Request, container: Annotated[ApplicationContainer, Depends(get_container)]
) -> str:
    """Служебный ключ разрешает контекст только от Gateway, не от браузера."""
    token = container.settings.internal_key.get_secret_value()
    supplied = request.headers.get("authorization", "")
    if not token or not secrets.compare_digest(
        supplied.encode("utf-8"), f"Bearer {token}".encode()
    ):
        raise HTTPException(403, "Доступ разрешён только API Gateway.")
    actor = request.headers.get("x-review-actor", "").strip()
    if not 1 <= len(actor) <= 128:
        raise HTTPException(403, "Отсутствует доверенный контекст инженера.")
    return actor
