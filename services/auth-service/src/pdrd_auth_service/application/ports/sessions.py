# services/auth-service/src/pdrd_auth_service/application/ports/sessions.py

"""Порты серверного хранилища сессий и актуального состояния профиля."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from pdrd_auth_service.domain.session import AuthSession


@dataclass(frozen=True, slots=True)
class UserAccessState:
    """Минимальные сведения от user-service для разрешения сессии."""

    user_id: UUID
    status: str
    authorization_version: int


class UserStatePort(Protocol):
    """Получает профиль через доверенный внутренний канал user-service."""

    async def get_state(self, user_id: UUID) -> UserAccessState | None:
        """Возвращает актуальные статус и версию полномочий."""
        ...


class SessionStorePort(Protocol):
    """Атомарно меняет только записи собственного хранилища auth-service."""

    async def create(self, session: AuthSession) -> None:
        """Сохраняет новую сессию с уникальным хешем секрета."""
        ...

    async def find_by_token_hash(self, token_hash: str) -> AuthSession | None:
        """Находит запись без передачи исходного секрета в базу."""
        ...

    async def renew_if_active(
        self, session: AuthSession, now: datetime, idle_timeout_seconds: int
    ) -> AuthSession | None:
        """Продлевает срок атомарно относительно отзыва и истечения."""
        ...

    async def revoke_token_hash(self, token_hash: str, now: datetime) -> None:
        """Завершает одну сессию по хешу её секрета."""
        ...

    async def revoke_user(self, user_id: UUID, now: datetime) -> int:
        """Завершает все активные сессии пользователя."""
        ...

    async def revoke_owned(
        self, user_id: UUID, session_id: UUID, now: datetime
    ) -> bool:
        """Завершает выбранную сессию только при совпадении владельца."""
        ...

    async def list_user(self, user_id: UUID, now: datetime) -> tuple[AuthSession, ...]:
        """Возвращает действующие записи пользователя для списка устройств."""
        ...
