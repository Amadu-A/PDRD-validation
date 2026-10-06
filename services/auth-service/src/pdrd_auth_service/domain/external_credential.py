# services/auth-service/src/pdrd_auth_service/domain/external_credential.py

"""Учётные данные внешнего пользователя, которыми владеет только Auth Service."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ExternalCredential:
    """Связывает устойчивый subject, хеш пароля и ожидающее подтверждение."""

    subject: UUID
    user_id: UUID | None
    email: str
    password_hash: str
    created_at: datetime
    verified_at: datetime | None = None
    verification_token_hash: str | None = None
    verification_expires_at: datetime | None = None

    def __post_init__(self) -> None:
        """Не допускает противоречивого состояния подтверждения."""
        if not isinstance(self.subject, UUID):
            raise TypeError("subject должен быть UUID")
        if self.user_id is not None and not isinstance(self.user_id, UUID):
            raise TypeError("user_id должен быть UUID")
        if not self.email or self.email != self.email.strip().lower():
            raise ValueError("Требуется нормализованный email")
        if not self.password_hash.startswith("scrypt$"):
            raise ValueError("Требуется хеш пароля scrypt")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at должен содержать timezone")
        if self.verified_at is not None and (
            self.verified_at.tzinfo is None or self.verified_at.utcoffset() is None
        ):
            raise ValueError("verified_at должен содержать timezone")
        if (self.verification_token_hash is None) != (
            self.verification_expires_at is None
        ):
            raise ValueError("Хеш и срок подтверждения задаются вместе")
