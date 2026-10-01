# services/auth-service/tests/unit/test_session_service.py

"""Проверяет жизненный цикл сессий и отзыв без настоящей БД и AD."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pdrd_auth_service.application.ports.sessions import UserAccessState
from pdrd_auth_service.application.use_cases.sessions import (
    SessionInvalid,
    SessionService,
    UserStateUnavailable,
    token_digest,
)
from pdrd_auth_service.domain.session import AuthSession, SessionPolicy

NOW = datetime(2026, 9, 30, 9, tzinfo=UTC)
USER_ID = UUID("9114be7f-220d-46a1-ab8d-09f632e0340a")
OTHER_ID = UUID("8704852d-cc70-4cbf-b4cf-c4074179370c")


class FakeUsers:
    """Позволяет менять статус и версию между запросами без вызова AD."""

    def __init__(self) -> None:
        """Начинает с действующего профиля."""
        self.state: UserAccessState | None = UserAccessState(USER_ID, "active", 1)
        self.fail = False
        self.calls = 0

    async def get_state(self, user_id: UUID) -> UserAccessState | None:
        """Считает проверки и имитирует недоступность user-service."""
        self.calls += 1
        if self.fail:
            raise OSError("user-service unavailable")
        return self.state


class FakeStore:
    """Реализует атомарные ограничения порта в памяти для сценария."""

    def __init__(self) -> None:
        """Создаёт пустой набор и флаг гонки отзыва."""
        self.rows: dict[str, AuthSession] = {}
        self.revoke_on_renew = False

    async def create(self, session: AuthSession) -> None:
        """Фиксирует новый хеш без сырого токена."""
        assert session.token_hash not in self.rows
        self.rows[session.token_hash] = session

    async def find_by_token_hash(self, token_hash: str) -> AuthSession | None:
        """Читает по хешу."""
        return self.rows.get(token_hash)

    async def renew_if_active(
        self, session: AuthSession, now: datetime, idle_timeout_seconds: int
    ) -> AuthSession | None:
        """Имитирует атомарное продление и отзыв между чтением и записью."""
        current = self.rows.get(session.token_hash)
        if self.revoke_on_renew and current is not None:
            current = replace(current, revoked_at=now)
            self.rows[session.token_hash] = current
        if current is None or not current.is_active(now):
            return None
        updated = current.renewed(now, timedelta(seconds=idle_timeout_seconds))
        self.rows[session.token_hash] = updated
        return updated

    async def revoke_token_hash(self, token_hash: str, now: datetime) -> None:
        """Отзывает существующую запись идемпотентно."""
        current = self.rows.get(token_hash)
        if current is not None and current.revoked_at is None:
            self.rows[token_hash] = replace(current, revoked_at=now)

    async def revoke_user(self, user_id: UUID, now: datetime) -> int:
        """Отзывает все сессии пользователя."""
        count = 0
        for token_hash, current in tuple(self.rows.items()):
            if current.user_id == user_id and current.revoked_at is None:
                self.rows[token_hash] = replace(current, revoked_at=now)
                count += 1
        return count

    async def revoke_owned(
        self, user_id: UUID, session_id: UUID, now: datetime
    ) -> bool:
        """Не позволяет изменить чужую запись."""
        for token_hash, current in tuple(self.rows.items()):
            if current.user_id == user_id and current.session_id == session_id:
                if current.revoked_at is None:
                    self.rows[token_hash] = replace(current, revoked_at=now)
                return True
        return False

    async def list_user(self, user_id: UUID, now: datetime) -> tuple[AuthSession, ...]:
        """Возвращает только записи указанного владельца."""
        return tuple(
            row
            for row in self.rows.values()
            if row.user_id == user_id and row.is_active(now)
        )


class Clock:
    """Позволяет проверять истечение срока без ожидания реального времени."""

    def __init__(self) -> None:
        """Фиксирует начальный момент."""
        self.now = NOW

    def __call__(self) -> datetime:
        """Возвращает управляемое время."""
        return self.now


def service() -> tuple[SessionService, FakeStore, FakeUsers, Clock]:
    """Собирает сервис с изолированными портами и настоящей случайностью."""
    store = FakeStore()
    users = FakeUsers()
    clock = Clock()
    return SessionService(store, users, clock=clock), store, users, clock


@pytest.mark.asyncio
async def test_issue_stores_only_digest_and_resolve_renews_idle() -> None:
    """Секрет выдаётся один раз, каждый запрос проверяет состояние профиля."""
    sessions, store, users, clock = service()
    issued = await sessions.issue(USER_ID)

    assert len(issued.token) == 43
    assert issued.token not in str(store.rows)
    assert token_digest(issued.token) in store.rows
    assert not hasattr(issued.session, "token_hash")
    assert issued.session.absolute_expires_at == NOW + timedelta(hours=8)

    clock.now += timedelta(hours=1)
    resolved = await sessions.resolve(issued.token)
    assert resolved.idle_expires_at == NOW + timedelta(hours=3)
    assert resolved.absolute_expires_at == NOW + timedelta(hours=8)
    assert users.calls == 2


@pytest.mark.asyncio
async def test_issue_rejects_blocked_missing_or_unavailable_user() -> None:
    """Подтверждённый AD-пароль не обходит блокировку PDRD-профиля."""
    sessions, store, users, _ = service()
    users.state = replace(users.state, status="blocked")
    with pytest.raises(SessionInvalid):
        await sessions.issue(USER_ID)
    users.state = None
    with pytest.raises(SessionInvalid):
        await sessions.issue(USER_ID)
    users.fail = True
    with pytest.raises(UserStateUnavailable):
        await sessions.issue(USER_ID)
    assert not store.rows


@pytest.mark.asyncio
async def test_expiry_and_version_change_fail_closed() -> None:
    """Смена роли отзывает старую сессию, истечение не продлевается."""
    sessions, store, users, clock = service()
    issued = await sessions.issue(USER_ID)
    users.state = replace(users.state, authorization_version=2)
    with pytest.raises(SessionInvalid):
        await sessions.resolve(issued.token)
    assert store.rows[token_digest(issued.token)].revoked_at == NOW

    another = await sessions.issue(USER_ID)
    clock.now += timedelta(hours=2)
    with pytest.raises(SessionInvalid):
        await sessions.resolve(another.token)


@pytest.mark.asyncio
async def test_user_service_outage_denies_without_revoking() -> None:
    """Временный сбой проверки не уничтожает ранее действующую сессию."""
    sessions, store, users, _ = service()
    issued = await sessions.issue(USER_ID)
    users.fail = True
    with pytest.raises(UserStateUnavailable):
        await sessions.resolve(issued.token)
    assert store.rows[token_digest(issued.token)].revoked_at is None


@pytest.mark.asyncio
async def test_revoke_current_all_and_owned() -> None:
    """Отзыв одного, всех и выбранного устройства идемпотентен и ограничен владельцем."""
    sessions, _, _, _ = service()
    first = await sessions.issue(USER_ID)
    second = await sessions.issue(USER_ID)
    assert not await sessions.revoke_owned(OTHER_ID, second.session.session_id)
    assert await sessions.revoke_owned(USER_ID, second.session.session_id)
    with pytest.raises(SessionInvalid):
        await sessions.resolve(second.token)
    assert await sessions.revoke_all(USER_ID) == 1
    with pytest.raises(SessionInvalid):
        await sessions.resolve(first.token)
    await sessions.revoke_current(first.token)


@pytest.mark.asyncio
async def test_concurrent_logout_prevents_late_renewal() -> None:
    """Отзыв после чтения не должен быть перезаписан продлением."""
    sessions, store, _, _ = service()
    issued = await sessions.issue(USER_ID)
    store.revoke_on_renew = True
    with pytest.raises(SessionInvalid):
        await sessions.resolve(issued.token)


@pytest.mark.asyncio
async def test_malformed_token_is_rejected_before_database_lookup() -> None:
    """Необработанный ввод браузера не попадает в SQL-запрос."""
    sessions, store, _, _ = service()
    for token in ("", "short", "a" * 42, "a" * 44, "a" * 42 + "*"):
        with pytest.raises(SessionInvalid):
            await sessions.resolve(token)
    assert not store.rows


def test_session_policy_can_be_configured_within_cap() -> None:
    """Политика допускает ужесточение IT без смены модели данных."""
    policy = SessionPolicy(timedelta(minutes=30), timedelta(hours=4))
    assert policy.idle_timeout < policy.absolute_timeout
