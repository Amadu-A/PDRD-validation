# services/user-service/tests/unit/test_external_accounts.py

"""Проверяет регистрацию и подтверждение без PostgreSQL и почтового сервера."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pdrd_user_service.application.ports.repository import (
    AuthorizationConflict,
    IdentityConflict,
)
from pdrd_user_service.application.use_cases.external_accounts import (
    ExternalAccountConflict,
    ExternalAccounts,
)
from pdrd_user_service.application.use_cases.users import UserDirectory, UserNotFound
from pdrd_user_service.domain.access import AccessTier
from pdrd_user_service.domain.identity import (
    ExternalIdentity,
    UserAccount,
    UserKind,
    UserStatus,
)

AT = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
USER_ID = UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")
SUBJECT = UUID("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb")
OTHER_SUBJECT = UUID("cccccccc-cccc-4ccc-cccc-cccccccccccc")


class MemoryUsers:
    """Хранит только поля, нужные жизненному циклу внешнего профиля."""

    def __init__(self) -> None:
        """Создаёт пустой каталог и счётчик активаций."""
        self.accounts: dict[UUID, UserAccount] = {}
        self.identities: dict[tuple[str, str, str], UUID] = {}
        self.activation_count = 0

    async def find_identity(
        self, provider_id: str, namespace: str, subject: str
    ) -> UserAccount | None:
        """Разрешает только точное совпадение устойчивого ключа."""
        user_id = self.identities.get((provider_id, namespace, subject))
        return self.accounts.get(user_id) if user_id is not None else None

    async def get_user(
        self, user_id: UUID, *, for_update: bool = False
    ) -> UserAccount | None:
        """Имитирует чтение профиля под блокировкой."""
        del for_update
        return self.accounts.get(user_id)

    async def create_user(self, user: UserAccount, identity: ExternalIdentity) -> None:
        """Не допускает вторую строку для того же stable key."""
        if identity.stable_key in self.identities:
            raise IdentityConflict("Занято")
        self.accounts[user.user_id] = user
        self.identities[identity.stable_key] = user.user_id

    async def activate_external(
        self, updated_user: UserAccount, expected_authorization_version: int
    ) -> None:
        """Проверяет версию и исходное состояние перед изменением."""
        current = self.accounts[updated_user.user_id]
        if (
            current.status is not UserStatus.PENDING_VERIFICATION
            or current.authorization_version != expected_authorization_version
        ):
            raise AuthorizationConflict("Состояние изменилось")
        self.accounts[updated_user.user_id] = updated_user
        self.activation_count += 1


class MemoryWork:
    """Имитирует явную транзакцию для сценариев каталога."""

    def __init__(self, users: MemoryUsers) -> None:
        """Передаёт один репозиторий всем последовательным командам."""
        self.users = users
        self.commits = 0

    async def __aenter__(self) -> "MemoryWork":
        """Открывает рабочую область."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Завершает рабочую область."""
        del args

    async def commit(self) -> None:
        """Отмечает успешную запись."""
        self.commits += 1


def directory(users: MemoryUsers) -> ExternalAccounts:
    """Создаёт сценарий с фиксированными временем и идентификатором."""
    return ExternalAccounts(
        lambda: MemoryWork(users), clock=lambda: AT, new_id=lambda: USER_ID
    )


@pytest.mark.asyncio
async def test_registration_remains_pending_and_retry_is_idempotent() -> None:
    """Повтор не создаёт новую запись и не активирует профиль без письма."""
    users = MemoryUsers()
    service = directory(users)
    first = await service.register(
        subject=SUBJECT, display_name="Внешний клиент", email="client@example.test"
    )
    repeated = await service.register(
        subject=SUBJECT, display_name="Другое имя", email="CLIENT@example.test"
    )
    assert repeated == first
    assert first.status is UserStatus.PENDING_VERIFICATION
    assert first.tier is AccessTier.REGISTERED_FREE
    assert first.authorization_version == 1
    assert first.last_login_at is None
    assert len(users.accounts) == 1
    assert users.identities == {("email", "pdrd", str(SUBJECT)): USER_ID}


@pytest.mark.asyncio
async def test_verification_requires_matching_identity_and_is_idempotent() -> None:
    """Только исходная учётная запись активирует свой профиль один раз."""
    users = MemoryUsers()
    service = directory(users)
    pending = await service.register(
        subject=SUBJECT, display_name="Клиент", email="client@example.test"
    )
    with pytest.raises(ExternalAccountConflict, match="Идентичность"):
        await service.verify_email(user_id=pending.user_id, subject=OTHER_SUBJECT)
    with pytest.raises(UserNotFound):
        await service.verify_email(user_id=OTHER_SUBJECT, subject=SUBJECT)
    active = await service.verify_email(user_id=pending.user_id, subject=SUBJECT)
    repeated = await service.verify_email(user_id=pending.user_id, subject=SUBJECT)
    assert active.status is UserStatus.ACTIVE
    assert active.authorization_version == 2
    assert repeated == active
    assert users.activation_count == 1


@pytest.mark.asyncio
async def test_blocked_account_cannot_be_re_registered_or_verified() -> None:
    """Блокировка сохраняется даже при повторной регистрации с тем же ключом."""
    users = MemoryUsers()
    service = directory(users)
    pending = await service.register(
        subject=SUBJECT, display_name="Клиент", email="client@example.test"
    )
    users.accounts[USER_ID] = replace(pending, status=UserStatus.BLOCKED)
    with pytest.raises(ExternalAccountConflict):
        await service.register(
            subject=SUBJECT, display_name="Клиент", email="client@example.test"
        )
    with pytest.raises(ExternalAccountConflict):
        await service.verify_email(user_id=USER_ID, subject=SUBJECT)
    assert users.activation_count == 0


@pytest.mark.asyncio
async def test_registration_rejects_rebinding_email_and_bad_address() -> None:
    """Занятый stable key не переходит к другому адресу или аккаунту."""
    users = MemoryUsers()
    service = directory(users)
    await service.register(
        subject=SUBJECT, display_name="Клиент", email="client@example.test"
    )
    with pytest.raises(ExternalAccountConflict):
        await service.register(
            subject=SUBJECT, display_name="Другой", email="other@example.test"
        )
    with pytest.raises(ValueError, match="почты"):
        await service.register(
            subject=OTHER_SUBJECT, display_name="Другой", email="bad"
        )
    assert len(users.accounts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_id", ["email", "EMAIL", " email "])
async def test_legacy_provision_cannot_activate_unverified_email(
    provider_id: str,
) -> None:
    """Старый API создания проверенных профилей не обходит email-подтверждение."""
    users = MemoryUsers()
    service = UserDirectory(lambda: MemoryWork(users), clock=lambda: AT)
    with pytest.raises(ValueError, match="подтверждением"):
        await service.provision(
            provider_id=provider_id,
            namespace="pdrd",
            subject=str(SUBJECT),
            kind=UserKind.EXTERNAL,
            display_name="Клиент",
            email="client@example.test",
        )
    assert users.accounts == {}
