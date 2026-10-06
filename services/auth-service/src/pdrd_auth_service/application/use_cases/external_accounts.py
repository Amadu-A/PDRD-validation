# services/auth-service/src/pdrd_auth_service/application/use_cases/external_accounts.py

"""Регистрация, подтверждение почты и вход внешнего пользователя.

Пароль остаётся только в памяти запроса; user-service получает лишь устойчивый
subject и открытые поля профиля. Код письма хранится только как SHA-256.
"""

import asyncio
import hashlib
import re
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pdrd_auth_service.application.ports.external_accounts import (
    ExternalCredentialStorePort,
    ExternalProfilePort,
    VerificationEmailPort,
)
from pdrd_auth_service.domain.external_credential import ExternalCredential
from pdrd_auth_service.domain.passwords import hash_password, verify_password

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_EMAIL_PATTERN = re.compile(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,63}\Z")


class ExternalCredentialsInvalid(PermissionError):
    """Вход или подтверждение не установили подлинность без уточнения причины."""


def normalize_email(raw: str) -> str:
    """Приводит адрес к одному ключу и отвергает опасный формат."""
    if not isinstance(raw, str):
        raise ValueError("Некорректный адрес электронной почты")
    email = raw.strip().lower()
    if (
        len(email) > 320
        or _EMAIL_PATTERN.fullmatch(email) is None
        or ".." in email
        or email.startswith(".")
        or ".@" in email
        or "@." in email
    ):
        raise ValueError("Некорректный адрес электронной почты")
    return email


class ExternalAccounts:
    """Координирует свою БД, закрытый API профилей и SMTP без общих таблиц."""

    def __init__(
        self,
        store: ExternalCredentialStorePort,
        profiles: ExternalProfilePort,
        email: VerificationEmailPort,
        *,
        clock: Callable[[], datetime] | None = None,
        new_subject: Callable[[], UUID] = uuid4,
        new_token: Callable[[int], str] = secrets.token_urlsafe,
    ) -> None:
        """Получает проверяемые порты и источники времени/случайности."""
        self._store = store
        self._profiles = profiles
        self._email = email
        self._clock = clock or (lambda: datetime.now(UTC))
        self._new_subject = new_subject
        self._new_token = new_token

    async def register(self, *, display_name: str, email: str, password: str) -> None:
        """Создаёт ожидающий профиль и отправляет ограниченный по сроку код."""
        address = normalize_email(email)
        name = display_name.strip() if isinstance(display_name, str) else ""
        if not 1 <= len(name) <= 255:
            raise ValueError("Имя должно содержать от 1 до 255 символов")
        current = await self._store.get_by_email(address)
        if current is None:
            encoded = await asyncio.to_thread(hash_password, password)
            subject = self._new_subject()
            created = ExternalCredential(
                subject=subject,
                user_id=None,
                email=address,
                password_hash=encoded,
                created_at=self._clock(),
            )
            if await self._store.create(created):
                current = created
            else:
                current = await self._store.get_by_email(address)
        if current is None or current.verified_at is not None:
            return
        matches = await asyncio.to_thread(
            verify_password, password, current.password_hash
        )
        if not matches:
            return
        user_id = await self._profiles.register_external(
            subject=current.subject, display_name=name, email=address
        )
        await self._store.attach_user(current.subject, user_id)
        token = self._new_token(32)
        if _TOKEN_PATTERN.fullmatch(token) is None:
            raise RuntimeError("Источник кода подтверждения вернул неверный формат")
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        await self._store.set_verification(
            current.subject, digest, self._clock() + timedelta(hours=24)
        )
        await self._email.send_verification(recipient=address, token=token)

    async def verify_email(self, token: str) -> None:
        """Активирует профиль и однократно погашает действующий код письма."""
        if not isinstance(token, str) or _TOKEN_PATTERN.fullmatch(token) is None:
            raise ExternalCredentialsInvalid("Ссылка подтверждения недействительна")
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        current = await self._store.get_by_token_hash(digest)
        now = self._clock()
        if (
            current is None
            or current.verified_at is not None
            or current.user_id is None
            or current.verification_expires_at is None
            or now >= current.verification_expires_at
        ):
            raise ExternalCredentialsInvalid("Ссылка подтверждения недействительна")
        await self._profiles.verify_external(
            user_id=current.user_id, subject=current.subject
        )
        if not await self._store.consume_verification(current.subject, digest, now):
            raise ExternalCredentialsInvalid("Ссылка подтверждения недействительна")

    async def authenticate(self, *, login: str, password: str) -> UUID:
        """Проверяет только подтверждённый внешний пароль без различимых ошибок."""
        try:
            address = normalize_email(login)
        except ValueError:
            raise ExternalCredentialsInvalid("Неверные учётные данные") from None
        current = await self._store.get_by_email(address)
        valid = await asyncio.to_thread(
            verify_password,
            password,
            current.password_hash if current is not None else None,
        )
        if (
            not valid
            or current is None
            or current.verified_at is None
            or current.user_id is None
        ):
            raise ExternalCredentialsInvalid("Неверные учётные данные")
        return current.user_id
