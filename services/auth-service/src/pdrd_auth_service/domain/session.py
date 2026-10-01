# services/auth-service/src/pdrd_auth_service/domain/session.py

"""Серверная сессия PDRD и ограничения её срока действия.

В доменной модели хранится только хеш случайного секрета. Права и роль не
копируются в сессию: их актуальность подтверждает владелец профиля.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from uuid import UUID


def _aware(value: datetime, name: str) -> None:
    """Отвергает время без часового пояса на границе домена."""
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{name} должен содержать часовой пояс")


@dataclass(frozen=True, slots=True)
class SessionPolicy:
    """Ограничивает обычную сессию независимо от будущего remember-grant."""

    idle_timeout: timedelta = timedelta(hours=2)
    absolute_timeout: timedelta = timedelta(hours=8)

    def __post_init__(self) -> None:
        """Не разрешает нулевой, отрицательный или чрезмерный срок."""
        if not timedelta(0) < self.idle_timeout <= timedelta(days=1):
            raise ValueError("Недопустимый срок неактивности сессии")
        if not self.idle_timeout <= self.absolute_timeout <= timedelta(days=1):
            raise ValueError("Недопустимый абсолютный срок сессии")


@dataclass(frozen=True, slots=True)
class AuthSession:
    """Описывает одну отзываемую сессию без браузерного секрета."""

    session_id: UUID
    user_id: UUID
    token_hash: str
    authorization_version: int
    created_at: datetime
    last_seen_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        """Проверяет сроки, UUID и формат SHA-256 до записи в хранилище."""
        if not isinstance(self.session_id, UUID) or not isinstance(self.user_id, UUID):
            raise TypeError("Идентификаторы сессии и пользователя должны быть UUID")
        if (
            not isinstance(self.token_hash, str)
            or len(self.token_hash) != 64
            or any(character not in "0123456789abcdef" for character in self.token_hash)
        ):
            raise ValueError("Требуется SHA-256 хеш секрета сессии")
        if (
            not isinstance(self.authorization_version, int)
            or isinstance(self.authorization_version, bool)
            or self.authorization_version < 1
        ):
            raise ValueError("Версия полномочий должна быть положительной")
        for name in (
            "created_at",
            "last_seen_at",
            "idle_expires_at",
            "absolute_expires_at",
        ):
            _aware(getattr(self, name), name)
        if self.revoked_at is not None:
            _aware(self.revoked_at, "revoked_at")
        if not (
            self.created_at
            <= self.last_seen_at
            < self.idle_expires_at
            <= self.absolute_expires_at
            <= self.created_at + timedelta(days=1)
        ):
            raise ValueError("Неверная хронология сессии")

    def is_active(self, now: datetime) -> bool:
        """Проверяет отзыв, простой и абсолютный предел на сервере."""
        _aware(now, "now")
        return (
            self.revoked_at is None
            and now < self.idle_expires_at
            and now < self.absolute_expires_at
        )

    def renewed(self, now: datetime, idle_timeout: timedelta) -> "AuthSession":
        """Продлевает только срок простоя, не сдвигая абсолютный предел."""
        if not self.is_active(now):
            raise ValueError("Недействительную сессию нельзя продлить")
        return replace(
            self,
            last_seen_at=max(self.last_seen_at, now),
            idle_expires_at=min(
                max(self.idle_expires_at, now + idle_timeout),
                self.absolute_expires_at,
            ),
        )
