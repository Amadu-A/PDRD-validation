# services/auth-service/src/pdrd_auth_service/infrastructure/database/external_credentials.py

"""Транзакционный PostgreSQL-адаптер внешнего входа и подтверждения email."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pdrd_auth_service.domain.external_credential import ExternalCredential
from pdrd_auth_service.infrastructure.database.models import ExternalCredentialModel


def _to_domain(row: ExternalCredentialModel) -> ExternalCredential:
    """Преобразует ORM-строку без передачи секрета браузеру."""
    return ExternalCredential(
        subject=row.subject,
        user_id=row.user_id,
        email=row.email,
        password_hash=row.password_hash,
        created_at=row.created_at,
        verified_at=row.verified_at,
        verification_token_hash=row.verification_token_hash,
        verification_expires_at=row.verification_expires_at,
    )


class SqlAlchemyExternalCredentialStore:
    """Использует только auth.external_credentials и условные SQL UPDATE."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Получает фабрику коротких транзакций собственной БД."""
        self._session_factory = session_factory

    async def get_by_email(self, email: str) -> ExternalCredential | None:
        """Находит нормализованный адрес по уникальному индексу."""
        async with self._session_factory() as database:
            row = await database.scalar(
                select(ExternalCredentialModel).where(
                    ExternalCredentialModel.email == email
                )
            )
            return _to_domain(row) if row is not None else None

    async def get_by_token_hash(self, digest: str) -> ExternalCredential | None:
        """Не передаёт исходный код подтверждения в БД."""
        async with self._session_factory() as database:
            row = await database.scalar(
                select(ExternalCredentialModel).where(
                    ExternalCredentialModel.verification_token_hash == digest
                )
            )
            return _to_domain(row) if row is not None else None

    async def create(self, credential: ExternalCredential) -> bool:
        """Разрешает гонку двух регистраций через уникальный ключ email."""
        async with self._session_factory() as database, database.begin():
            result = await database.execute(
                insert(ExternalCredentialModel)
                .values(
                    subject=credential.subject,
                    user_id=credential.user_id,
                    email=credential.email,
                    password_hash=credential.password_hash,
                    created_at=credential.created_at,
                )
                .on_conflict_do_nothing(index_elements=["email"])
            )
            return result.rowcount == 1

    async def attach_user(self, subject: UUID, user_id: UUID) -> None:
        """Не допускает смены связанного UUID после регистрации."""
        async with self._session_factory() as database, database.begin():
            result = await database.execute(
                update(ExternalCredentialModel)
                .where(
                    ExternalCredentialModel.subject == subject,
                    (
                        ExternalCredentialModel.user_id.is_(None)
                        | (ExternalCredentialModel.user_id == user_id)
                    ),
                )
                .values(user_id=user_id)
            )
            if result.rowcount != 1:
                raise ValueError("Связанный профиль внешней учётной записи изменился")

    async def set_verification(
        self, subject: UUID, digest: str, expires_at: datetime
    ) -> None:
        """Обновляет код лишь пока профиль ожидает подтверждения."""
        async with self._session_factory() as database, database.begin():
            result = await database.execute(
                update(ExternalCredentialModel)
                .where(
                    ExternalCredentialModel.subject == subject,
                    ExternalCredentialModel.user_id.is_not(None),
                    ExternalCredentialModel.verified_at.is_(None),
                )
                .values(
                    verification_token_hash=digest,
                    verification_expires_at=expires_at,
                )
            )
            if result.rowcount != 1:
                raise ValueError("Подтверждение уже завершено")

    async def consume_verification(
        self, subject: UUID, digest: str, now: datetime
    ) -> bool:
        """Погашает только совпадающий, ещё действующий код одним UPDATE."""
        async with self._session_factory() as database, database.begin():
            result = await database.execute(
                update(ExternalCredentialModel)
                .where(
                    ExternalCredentialModel.subject == subject,
                    ExternalCredentialModel.verification_token_hash == digest,
                    ExternalCredentialModel.verification_expires_at > now,
                    ExternalCredentialModel.verified_at.is_(None),
                )
                .values(
                    verified_at=now,
                    verification_token_hash=None,
                    verification_expires_at=None,
                )
            )
            return result.rowcount == 1
