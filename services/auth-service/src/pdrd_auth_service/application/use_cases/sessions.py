# services/auth-service/src/pdrd_auth_service/application/use_cases/sessions.py

"""Выпуск, разрешение и отзыв непрозрачных серверных сессий.

Этот сценарий не публикует HTTP-маршруты. Будущий транспорт обязан выставлять
Secure/HttpOnly cookie, защищать изменяющие запросы от CSRF и ограничивать
частоту попыток входа до вызова выдачи сессии.
"""

import hashlib
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pdrd_auth_service.application.ports.sessions import SessionStorePort, UserStatePort
from pdrd_auth_service.domain.session import AuthSession, SessionPolicy

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")


class SessionInvalid(PermissionError):
    """Секрет, срок, профиль или версия полномочий не подтверждены."""


class UserStateUnavailable(RuntimeError):
    """Каталог пользователей не подтвердил актуальный доступ."""


@dataclass(frozen=True, slots=True)
class SessionView:
    """Метаданные для доверенного транспорта без хеша браузерного секрета."""

    session_id: UUID
    user_id: UUID
    authorization_version: int
    created_at: datetime
    last_seen_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """Передаёт новый секрет только будущему HTTP-транспорту для cookie."""

    token: str
    session: SessionView


def _view(session: AuthSession) -> SessionView:
    """Удаляет хеш токена из результата прикладного сценария."""
    return SessionView(
        session_id=session.session_id,
        user_id=session.user_id,
        authorization_version=session.authorization_version,
        created_at=session.created_at,
        last_seen_at=session.last_seen_at,
        idle_expires_at=session.idle_expires_at,
        absolute_expires_at=session.absolute_expires_at,
    )


def token_digest(token: str) -> str:
    """Хеширует только корректный секрет фиксированной длины."""
    if not isinstance(token, str) or _TOKEN_PATTERN.fullmatch(token) is None:
        raise SessionInvalid("Сессия недействительна")
    return hashlib.sha256(token.encode("ascii")).hexdigest()


class SessionService:
    """Проверяет профиль при выпуске и каждом разрешении сессии без вызова AD."""

    def __init__(
        self,
        store: SessionStorePort,
        users: UserStatePort,
        *,
        policy: SessionPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
        new_id: Callable[[], UUID] = uuid4,
        new_token: Callable[[int], str] = secrets.token_urlsafe,
    ) -> None:
        """Получает только порты и управляемые источники времени и случайности."""
        self._store = store
        self._users = users
        self._policy = policy or SessionPolicy()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._new_id = new_id
        self._new_token = new_token

    async def _active_user(self, user_id: UUID) -> int:
        """Запрещает выпуск или использование сессии при сбое каталога."""
        try:
            state = await self._users.get_state(user_id)
        except Exception:
            raise UserStateUnavailable("Состояние пользователя недоступно") from None
        if state is None or state.user_id != user_id or state.status != "active":
            raise SessionInvalid("Сессия недействительна")
        if (
            not isinstance(state.authorization_version, int)
            or isinstance(state.authorization_version, bool)
            or state.authorization_version < 1
        ):
            raise SessionInvalid("Сессия недействительна")
        return state.authorization_version

    async def issue(self, user_id: UUID) -> IssuedSession:
        """Создаёт новый секрет после проверки статуса пользователя."""
        version = await self._active_user(user_id)
        now = self._clock()
        token = self._new_token(32)
        digest = token_digest(token)
        absolute_expiry = now + self._policy.absolute_timeout
        session = AuthSession(
            session_id=self._new_id(),
            user_id=user_id,
            token_hash=digest,
            authorization_version=version,
            created_at=now,
            last_seen_at=now,
            idle_expires_at=min(now + self._policy.idle_timeout, absolute_expiry),
            absolute_expires_at=absolute_expiry,
        )
        await self._store.create(session)
        return IssuedSession(token=token, session=_view(session))

    async def resolve(self, token: str) -> SessionView:
        """Проверяет срок, отзыв и свежую версию доступа перед продлением."""
        digest = token_digest(token)
        session = await self._store.find_by_token_hash(digest)
        now = self._clock()
        if session is None or not session.is_active(now):
            raise SessionInvalid("Сессия недействительна")
        try:
            version = await self._active_user(session.user_id)
        except SessionInvalid:
            await self._store.revoke_token_hash(digest, now)
            raise
        if version != session.authorization_version:
            await self._store.revoke_token_hash(digest, now)
            raise SessionInvalid("Сессия недействительна")
        renewed = await self._store.renew_if_active(
            session, now, int(self._policy.idle_timeout.total_seconds())
        )
        if renewed is None:
            raise SessionInvalid("Сессия недействительна")
        return _view(renewed)

    async def revoke_current(self, token: str) -> None:
        """Завершает текущую сессию без раскрытия существования секрета."""
        await self._store.revoke_token_hash(token_digest(token), self._clock())

    async def revoke_all(self, user_id: UUID) -> int:
        """Отзывает все сессии по уже удостоверенному user_id."""
        return await self._store.revoke_user(user_id, self._clock())

    async def revoke_owned(self, user_id: UUID, session_id: UUID) -> bool:
        """Не даёт отозвать сессию другого пользователя."""
        return await self._store.revoke_owned(user_id, session_id, self._clock())

    async def list_user(self, user_id: UUID) -> tuple[SessionView, ...]:
        """Возвращает метаданные без секрета после проверки владельца транспортом."""
        return tuple(
            _view(session)
            for session in await self._store.list_user(user_id, self._clock())
        )
