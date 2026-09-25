# services/experience-service/src/pdrd_experience_service/transport/http/dependencies.py

"""Получение зависимостей HTTP-запросов Experience Service.

Контейнер создаётся при инициализации FastAPI-приложения
и сохраняется в application.state.

HTTP endpoints не создают SQLAlchemy engine, репозитории
или другие инфраструктурные компоненты самостоятельно.
"""

from fastapi import Request

from pdrd_experience_service.core.container import (
    ApplicationContainer,
)


def get_container(
    request: Request,
) -> ApplicationContainer:
    """Возвращает общий контейнер текущего приложения."""
    return request.app.state.container
