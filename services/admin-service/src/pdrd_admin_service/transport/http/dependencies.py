# services/admin-service/src/pdrd_admin_service/transport/http/dependencies.py

"""Достаёт административный use case из текущего экземпляра FastAPI."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from pdrd_admin_service.application.admin_organizations import AdminOrganizations
from pdrd_admin_service.application.admin_users import AdminUsers
from pdrd_admin_service.core.container import ApplicationContainer


def get_container(request: Request) -> ApplicationContainer:
    """Не смешивает зависимости разных экземпляров приложения в тестах."""
    return request.app.state.container


Container = Annotated[ApplicationContainer, Depends(get_container)]


def get_admin_users(container: Container) -> AdminUsers:
    """Отклоняет запрос, если процесс не настроен для работы."""
    if container.admin_users is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Сервис недоступен.")
    return container.admin_users


Admin = Annotated[AdminUsers, Depends(get_admin_users)]


def get_admin_organizations(container: Container) -> AdminOrganizations:
    """Отклоняет организационные операции у выключенного процесса."""
    if container.admin_organizations is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Сервис недоступен.")
    return container.admin_organizations


Organizations = Annotated[AdminOrganizations, Depends(get_admin_organizations)]
