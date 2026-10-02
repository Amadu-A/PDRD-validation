# services/auth-service/tests/unit/test_external_accounts.py

"""Проверяет регистрацию, письмо, подтверждение и внешний вход без SMTP/БД."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pdrd_auth_service.application.use_cases.external_accounts import (
    ExternalAccounts,
    ExternalCredentialsInvalid,
    normalize_email,
)
from pdrd_auth_service.domain.external_credential import ExternalCredential
from pdrd_auth_service.domain.passwords import hash_password, verify_password

NOW = datetime(2026, 10, 1, 9, tzinfo=UTC)
SUBJECT = UUID("8b968169-596a-40b0-a156-1d67e7df7cbd")
USER_ID = UUID("19489999-a5a2-45e5-a91e-11186e9ce377")


class FakeStore:
    """Хранит только доменные снимки и имитирует одноразовую операцию."""

    def __init__(self) -> None:
        """Создаёт пустой набор внешних учётных данных."""
        self.rows: dict[str, ExternalCredential] = {}

    async def get_by_email(self, email: str) -> ExternalCredential | None:
        """Ищет по нормализованному email."""
        return self.rows.get(email)

    async def get_by_token_hash(self, digest: str) -> ExternalCredential | None:
        """Ищет только хеш кода подтверждения."""
        return next(
            (
                row
                for row in self.rows.values()
                if row.verification_token_hash == digest
            ),
            None,
        )

    async def create(self, credential: ExternalCredential) -> bool:
        """Не заменяет уже существующую учётную запись."""
        if credential.email in self.rows:
            return False
        self.rows[credential.email] = credential
        return True

    async def attach_user(self, subject: UUID, user_id: UUID) -> None:
        """Фиксирует профиль только того же subject."""
        for email, row in self.rows.items():
            if row.subject == subject:
                self.rows[email] = replace(row, user_id=user_id)
                return
        raise AssertionError("subject missing")

    async def set_verification(
        self, subject: UUID, digest: str, expires_at: datetime
    ) -> None:
        """Сохраняет хеш вместо исходного токена."""
        for email, row in self.rows.items():
            if row.subject == subject:
                self.rows[email] = replace(
                    row,
                    verification_token_hash=digest,
                    verification_expires_at=expires_at,
                )
                return
        raise AssertionError("subject missing")

    async def consume_verification(
        self, subject: UUID, digest: str, now: datetime
    ) -> bool:
        """Повторное подтверждение не меняет состояние."""
        for email, row in self.rows.items():
            if row.subject == subject and row.verification_token_hash == digest:
                self.rows[email] = replace(
                    row,
                    verified_at=now,
                    verification_token_hash=None,
                    verification_expires_at=None,
                )
                return True
        return False


class FakeProfiles:
    """Считает вызовы user-service без передачи пароля и кода."""

    def __init__(self) -> None:
        """Начинает без зарегистрированного профиля."""
        self.register_calls: list[dict[str, object]] = []
        self.verify_calls: list[dict[str, object]] = []

    async def register_external(
        self, *, subject: UUID, display_name: str, email: str
    ) -> UUID:
        """Создаёт один ожидающий профиль."""
        self.register_calls.append(locals())
        return USER_ID

    async def verify_external(self, *, user_id: UUID, subject: UUID) -> None:
        """Активирует профиль после проверки токена Auth Service."""
        self.verify_calls.append(locals())


class FakeEmail:
    """Сохраняет только факт отправки и временный код теста."""

    def __init__(self) -> None:
        """Создаёт пустую исходящую почту."""
        self.sent: list[tuple[str, str]] = []

    async def send_verification(self, *, recipient: str, token: str) -> None:
        """Запоминает отправленный код для функциональной проверки."""
        self.sent.append((recipient, token))


def _service() -> tuple[ExternalAccounts, FakeStore, FakeProfiles, FakeEmail]:
    """Собирает сценарий с постоянным временем и UUID."""
    store = FakeStore()
    profiles = FakeProfiles()
    email = FakeEmail()
    return (
        ExternalAccounts(
            store,
            profiles,
            email,
            clock=lambda: NOW,
            new_subject=lambda: SUBJECT,
        ),
        store,
        profiles,
        email,
    )


@pytest.mark.asyncio
async def test_register_verify_then_login() -> None:
    """Профиль активируется только после действительного письма."""
    accounts, store, profiles, mail = _service()
    await accounts.register(
        display_name="Тестовый пользователь",
        email="  PERSON@EXAMPLE.COM  ",
        password="strong-test-password-2026",
    )
    row = store.rows["person@example.com"]
    assert row.user_id == USER_ID
    assert row.verified_at is None
    assert row.verification_expires_at == NOW + timedelta(hours=24)
    assert row.password_hash != "strong-test-password-2026"
    assert profiles.register_calls[0]["email"] == "person@example.com"
    assert "password" not in profiles.register_calls[0]
    recipient, token = mail.sent[0]
    assert recipient == "person@example.com"
    assert token not in str(store.rows)
    with pytest.raises(ExternalCredentialsInvalid):
        await accounts.authenticate(
            login="person@example.com", password="strong-test-password-2026"
        )

    await accounts.verify_email(token)
    assert profiles.verify_calls[0]["subject"] == SUBJECT
    assert (
        await accounts.authenticate(
            login="PERSON@example.com", password="strong-test-password-2026"
        )
        == USER_ID
    )
    with pytest.raises(ExternalCredentialsInvalid):
        await accounts.verify_email(token)


@pytest.mark.asyncio
async def test_existing_or_wrong_password_does_not_take_over_email() -> None:
    """Повторная регистрация не заменяет пароль чужого подтверждённого аккаунта."""
    accounts, store, profiles, mail = _service()
    await accounts.register(
        display_name="Первый",
        email="person@example.com",
        password="first-password-2026",
    )
    await accounts.register(
        display_name="Захват",
        email="person@example.com",
        password="other-password-2026",
    )
    assert len(mail.sent) == 1
    assert len(profiles.register_calls) == 1
    assert verify_password(
        "first-password-2026", store.rows["person@example.com"].password_hash
    )

    await accounts.verify_email(mail.sent[0][1])
    await accounts.register(
        display_name="Захват",
        email="person@example.com",
        password="other-password-2026",
    )
    assert len(mail.sent) == 1


@pytest.mark.asyncio
async def test_unknown_email_and_bad_token_are_rejected() -> None:
    """Не раскрывает существование email и не принимает короткий токен."""
    accounts, _, _, _ = _service()
    with pytest.raises(ExternalCredentialsInvalid, match="Неверные"):
        await accounts.authenticate(
            login="unknown@example.com", password="strong-test-password-2026"
        )
    with pytest.raises(ExternalCredentialsInvalid, match="недействительна"):
        await accounts.verify_email("short")


def test_password_hash_and_email_validation() -> None:
    """Scrypt использует соль, а ключ email нормализуется."""
    first = hash_password("strong-test-password-2026")
    second = hash_password("strong-test-password-2026")
    assert first != second
    assert verify_password("strong-test-password-2026", first)
    assert not verify_password("wrong-password-2026", first)
    assert normalize_email(" PERSON@EXAMPLE.COM ") == "person@example.com"
    with pytest.raises(ValueError):
        normalize_email("bad..person@example.com")
