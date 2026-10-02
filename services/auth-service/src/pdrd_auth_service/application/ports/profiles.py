# services/auth-service/src/pdrd_auth_service/application/ports/profiles.py

"""Безопасный снимок профиля и полномочий из каталога User Service."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ProfileSnapshot:
    """Поля личного кабинета без пароля и служебного ключа."""

    user_id: UUID
    kind: str
    tier: str
    status: str
    display_name: str
    login: str | None
    email: str | None
    authorization_version: int


@dataclass(frozen=True, slots=True)
class PermissionSnapshot:
    """Роли и операции в версии, согласованной с сессией."""

    user_id: UUID
    authorization_version: int
    status: str
    tier: str
    roles: tuple[str, ...]
    permissions: tuple[str, ...]


class ProfilePort(Protocol):
    """Читает актуальные данные через внутренний API владельца профиля."""

    async def get_user(self, user_id: UUID) -> ProfileSnapshot | None:
        """Возвращает профиль по UUID."""
        ...

    async def get_permissions(self, user_id: UUID) -> PermissionSnapshot:
        """Возвращает разрешения с версией."""
        ...
