# services/auth-service/src/pdrd_auth_service/application/ports/external_accounts.py

"""Порты хранения внешнего входа, каталога профилей и отправки письма."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from pdrd_auth_service.domain.external_credential import ExternalCredential


class ExternalCredentialStorePort(Protocol):
    """Хранит пароль только как хеш и одноразовый код только как SHA-256."""

    async def get_by_email(self, email: str) -> ExternalCredential | None:
        """Находит учётную запись по нормализованному email."""
        ...

    async def get_by_token_hash(self, digest: str) -> ExternalCredential | None:
        """Находит ещё не подтверждённую запись по хешу кода."""
        ...

    async def create(self, credential: ExternalCredential) -> bool:
        """Создаёт запись один раз и возвращает False при занятом email."""
        ...

    async def attach_user(self, subject: UUID, user_id: UUID) -> None:
        """Связывает учётные данные с ожидающим профилем в user-service."""
        ...

    async def set_verification(
        self, subject: UUID, digest: str, expires_at: datetime
    ) -> None:
        """Заменяет код только для ещё не подтверждённой записи."""
        ...

    async def consume_verification(
        self, subject: UUID, digest: str, now: datetime
    ) -> bool:
        """Однократно подтверждает действующий код атомарным UPDATE."""
        ...


class ExternalProfilePort(Protocol):
    """Управляет профилем через закрытый API user-service."""

    async def register_external(
        self, *, subject: UUID, display_name: str, email: str
    ) -> UUID:
        """Создаёт или находит ожидающий профиль и возвращает его UUID."""
        ...

    async def verify_external(self, *, user_id: UUID, subject: UUID) -> None:
        """Активирует профиль после доказательства владения адресом."""
        ...


class VerificationEmailPort(Protocol):
    """Отправляет ссылку с кодом, не включая её в журналы."""

    async def send_verification(self, *, recipient: str, token: str) -> None:
        """Отправляет письмо на указанный при регистрации адрес."""
        ...
