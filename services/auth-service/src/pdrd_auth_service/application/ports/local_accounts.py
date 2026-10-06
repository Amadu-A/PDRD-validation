# services/auth-service/src/pdrd_auth_service/application/ports/local_accounts.py

"""Порты собственного хранилища локальных паролей и каталога профилей."""

from typing import Protocol
from uuid import UUID

from pdrd_auth_service.domain.local_credential import LocalCredential


class LocalCredentialStorePort(Protocol):
    """Хранит локальную идентичность независимо от user-service."""

    async def get_by_username(self, username: str) -> LocalCredential | None:
        """Находит нормализованный уникальный логин."""
        ...

    async def create(self, credential: LocalCredential) -> bool:
        """Резервирует логин; конкурентное создание возвращает False."""
        ...

    async def attach_user(self, subject: UUID, user_id: UUID) -> None:
        """Однократно связывает пароль с подтверждённым профилем."""
        ...


class LocalSuperuserProfilePort(Protocol):
    """Создаёт первый профиль и административную роль через закрытый API."""

    async def provision_local_superuser(self, *, subject: UUID, username: str) -> UUID:
        """Идемпотентно завершает bootstrap той же устойчивой идентичности."""
        ...
