# services/user-service/src/pdrd_user_service/application/use_cases/external_accounts.py

"""Профили для входа по email: ожидание подтверждения и активация.

Auth Service проверяет владение адресом и хранит пароль. Этот сценарий получает
только устойчивый UUID его учётной записи и не принимает секреты браузера.
"""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pdrd_user_service.application.ports.repository import (
    IdentityConflict,
    UnitOfWorkFactory,
)
from pdrd_user_service.application.use_cases.users import UserNotFound
from pdrd_user_service.domain.access import AccessTier
from pdrd_user_service.domain.identity import (
    ExternalIdentity,
    UserAccount,
    UserKind,
    UserStatus,
)

EMAIL_PROVIDER = "email"
EMAIL_NAMESPACE = "pdrd"


class ExternalAccountConflict(Exception):
    """Профиль нельзя создать повторно или активировать с этим ключом."""


class ExternalAccounts:
    """Меняет только состояние внешнего профиля без учётных данных."""

    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        *,
        clock: Callable[[], datetime] | None = None,
        new_id: Callable[[], UUID] = uuid4,
    ) -> None:
        """Получает транзакционный порт и управляемые источники времени/UUID."""
        self._unit_of_work = unit_of_work
        self._clock = clock or (lambda: datetime.now(UTC))
        self._new_id = new_id

    async def register(
        self, *, subject: UUID, display_name: str, email: str
    ) -> UserAccount:
        """Создаёт бесплатный профиль без прав до подтверждения адреса."""
        if not isinstance(subject, UUID):
            raise TypeError("subject должен быть UUID")
        normalized_email = email.strip()
        if not normalized_email or "@" not in normalized_email:
            raise ValueError("Требуется адрес электронной почты")
        key = (EMAIL_PROVIDER, EMAIL_NAMESPACE, str(subject))
        try:
            async with self._unit_of_work() as work:
                existing = await work.users.find_identity(*key)
                if existing is not None:
                    return self._same_account(existing, normalized_email)
                user = UserAccount(
                    user_id=self._new_id(),
                    kind=UserKind.EXTERNAL,
                    tier=AccessTier.REGISTERED_FREE,
                    status=UserStatus.PENDING_VERIFICATION,
                    display_name=display_name,
                    email=normalized_email,
                    created_at=self._clock(),
                )
                await work.users.create_user(
                    user,
                    ExternalIdentity(*key, user.user_id),
                )
                await work.commit()
                return user
        except IdentityConflict:
            # Параллельные запросы могли создать тот же stable key раньше нас.
            async with self._unit_of_work() as work:
                existing = await work.users.find_identity(*key)
                if existing is None:
                    raise
                return self._same_account(existing, normalized_email)

    async def verify_email(self, *, user_id: UUID, subject: UUID) -> UserAccount:
        """После проверки токена в Auth Service активирует связанный профиль."""
        if not isinstance(user_id, UUID) or not isinstance(subject, UUID):
            raise TypeError("user_id и subject должны быть UUID")
        async with self._unit_of_work() as work:
            user = await work.users.get_user(user_id, for_update=True)
            if user is None:
                raise UserNotFound(user_id)
            linked = await work.users.find_identity(
                EMAIL_PROVIDER, EMAIL_NAMESPACE, str(subject)
            )
            if linked is None or linked.user_id != user_id:
                raise ExternalAccountConflict("Идентичность не принадлежит профилю")
            if user.kind is not UserKind.EXTERNAL or user.status is UserStatus.BLOCKED:
                raise ExternalAccountConflict("Профиль не может быть активирован")
            if user.status is UserStatus.ACTIVE:
                return user
            updated = replace(
                user,
                status=UserStatus.ACTIVE,
                authorization_version=user.authorization_version + 1,
            )
            await work.users.activate_external(updated, user.authorization_version)
            await work.commit()
            return updated

    @staticmethod
    def _same_account(user: UserAccount, email: str) -> UserAccount:
        """Повтор допускается только для той же почты и незаблокированного профиля."""
        if (
            user.kind is not UserKind.EXTERNAL
            or user.status is UserStatus.BLOCKED
            or user.email is None
            or user.email.casefold() != email.casefold()
        ):
            raise ExternalAccountConflict("Идентичность относится к иному профилю")
        return user
