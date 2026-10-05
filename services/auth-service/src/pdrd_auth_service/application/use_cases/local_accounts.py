# services/auth-service/src/pdrd_auth_service/application/use_cases/local_accounts.py

"""Создание первого локального суперпользователя и проверка его пароля.

Резервирование логина предшествует bootstrap профиля. После потери ответа
повтор с тем же паролем завершает операцию с прежним subject без нового UUID.
"""

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pdrd_auth_service.application.ports.local_accounts import (
    LocalCredentialStorePort,
    LocalSuperuserProfilePort,
)
from pdrd_auth_service.core.observability import log_execution_time
from pdrd_auth_service.domain.local_credential import (
    LocalCredential,
    normalize_username,
)
from pdrd_auth_service.domain.passwords import hash_password, verify_password


class LocalAccountConflict(ValueError):
    """Логин занят или незавершённое создание подтверждено другим паролем."""


class LocalAccounts:
    """Работает через порты; не читает таблицы пользователей напрямую."""

    def __init__(
        self, store: LocalCredentialStorePort, profiles: LocalSuperuserProfilePort
    ) -> None:
        """Получает изолированное хранилище хешей и доверенный каталог."""
        self._store = store
        self._profiles = profiles

    @log_execution_time(operation="local_superuser_create")
    async def create_superuser(self, *, username: str, password: str) -> UUID:
        """Создаёт суперпользователя или восстанавливает прерванный bootstrap."""
        username = normalize_username(username)
        current = await self._store.get_by_username(username)
        if current is None:
            encoded = await asyncio.to_thread(hash_password, password)
            candidate = LocalCredential(uuid4(), username, encoded, datetime.now(UTC))
            current = (
                candidate
                if await self._store.create(candidate)
                else await self._store.get_by_username(username)
            )
        if current is None or current.user_id is not None:
            raise LocalAccountConflict("Локальная учётная запись уже существует")
        if not await asyncio.to_thread(
            verify_password, password, current.password_hash
        ):
            raise LocalAccountConflict("Не удалось продолжить создание с этим паролем")
        user_id = await self._profiles.provision_local_superuser(
            subject=current.subject, username=username
        )
        await self._store.attach_user(current.subject, user_id)
        return user_id

    async def authenticate(self, *, username: str, password: str) -> UUID:
        """Проверяет локальный пароль; неверный пароль никогда не уходит в AD."""
        try:
            username = normalize_username(username)
        except ValueError:
            raise PermissionError("Неверные учётные данные") from None
        current = await self._store.get_by_username(username)
        valid = await asyncio.to_thread(
            verify_password, password, current.password_hash if current else None
        )
        if not valid or current is None or current.user_id is None:
            raise PermissionError("Неверные учётные данные")
        return current.user_id
