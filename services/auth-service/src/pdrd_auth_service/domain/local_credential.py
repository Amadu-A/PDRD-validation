# services/auth-service/src/pdrd_auth_service/domain/local_credential.py

"""Локальная учётная запись с хешем пароля, независимая от AD и SMTP."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

_USERNAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")


def normalize_username(value: str) -> str:
    """Возвращает единый ключ локального логина без email и управляющих символов."""
    if isinstance(value, str) and any(ord(char) < 32 for char in value):
        raise ValueError("Имя пользователя содержит управляющие символы")
    username = value.strip().lower() if isinstance(value, str) else ""
    if _USERNAME.fullmatch(username) is None:
        raise ValueError("Имя: 1–64 латинских буквы, цифры, точка, дефис или _")
    return username


@dataclass(frozen=True, slots=True)
class LocalCredential:
    """До привязки профиля вход закрыт; секрет не попадает в repr."""

    subject: UUID
    username: str
    password_hash: str = field(repr=False)
    created_at: datetime
    user_id: UUID | None = None
