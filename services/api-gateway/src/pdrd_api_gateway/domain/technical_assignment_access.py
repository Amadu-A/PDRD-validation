# services/api-gateway/src/pdrd_api_gateway/domain/technical_assignment_access.py

"""Stateless HMAC-доступ к подготовленному техническому заданию.

Токен привязан к UUID ТЗ и сроку действия. Секрет остаётся только в Gateway,
поэтому в Knowledge Service не нужна новая таблица пользовательских ключей.
"""

import base64
import hmac
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID

_PURPOSE = b"pdrd:technical-assignment-access:v1\0"
_LIFETIME = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class TechnicalAssignmentGrant:
    """Открытый токен и точное серверное время его истечения."""

    token: str = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class TechnicalAssignmentCapability:
    """Подписывает и проверяет доступ без сохранения токена или пароля."""

    secret: str = field(repr=False)

    def __post_init__(self) -> None:
        """Требует отдельный длинный ключ для включённого auth-контура."""
        if len(self.secret) < 32:
            raise ValueError("Ключ доступа к ТЗ должен содержать не меньше 32 символов")

    def issue(
        self, technical_assignment_id: UUID, *, now: datetime | None = None
    ) -> TechnicalAssignmentGrant:
        """Создаёт токен на 24 часа для одного неизменяемого UUID ТЗ."""
        current = now or datetime.now(UTC)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("Время выдачи должно содержать timezone")
        expires_at = datetime.fromtimestamp(int((current + _LIFETIME).timestamp()), UTC)
        seconds = int(expires_at.timestamp())
        signature = self._signature(technical_assignment_id, seconds)
        return TechnicalAssignmentGrant(f"v1.{seconds}.{signature}", expires_at)

    def verify(
        self,
        technical_assignment_id: UUID,
        token: str | None,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Сверяет UUID, HMAC и expiry без раскрытия причины отказа."""
        if not token or len(token) > 128:
            return False
        parts = token.split(".")
        if (
            len(parts) != 3
            or parts[0] != "v1"
            or not parts[1].isascii()
            or not parts[1].isdecimal()
        ):
            return False
        try:
            seconds = int(parts[1])
            expires_at = datetime.fromtimestamp(seconds, UTC)
        except (ValueError, OverflowError, OSError):
            return False
        current = now or datetime.now(UTC)
        if (
            current.tzinfo is None
            or current.utcoffset() is None
            or current >= expires_at
        ):
            return False
        expected = self._signature(technical_assignment_id, seconds)
        return hmac.compare_digest(parts[2], expected)

    def _signature(self, technical_assignment_id: UUID, seconds: int) -> str:
        """Изолирует подпись ТЗ от других применений ключа HMAC."""
        message = _PURPOSE + technical_assignment_id.bytes + seconds.to_bytes(8, "big")
        digest = hmac.new(self.secret.encode("utf-8"), message, sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
