# services/auth-service/tests/integration/test_session_database.py

"""Проверяет настоящие транзакции и миграцию на изолированном PostgreSQL."""

import asyncio
import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from pdrd_auth_service.application.ports.sessions import UserAccessState
from pdrd_auth_service.application.use_cases.sessions import (
    SessionInvalid,
    SessionService,
    token_digest,
)
from pdrd_auth_service.infrastructure.database.engine import build_session_factory
from pdrd_auth_service.infrastructure.database.models import SessionModel
from pdrd_auth_service.infrastructure.database.repository import SqlAlchemySessionStore
from sqlalchemy import delete, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

pytestmark = pytest.mark.database

TEST_HOST = "auth-test-postgres"
TEST_NAME = "pdrd_auth_test"
TEST_USER = "auth_test"
EXPECTED_REVISION = "20260930_auth_0001"


def validate_isolated_database_url(raw_url: str) -> URL:
    """Запрещает направлять интеграционные тесты в рабочую БД."""
    try:
        url = make_url(raw_url)
    except Exception:
        raise ValueError("Некорректный URL тестовой базы") from None
    if (
        url.drivername != "postgresql+asyncpg"
        or url.host != TEST_HOST
        or url.port not in (None, 5432)
        or url.database != TEST_NAME
        or url.username != TEST_USER
        or not url.password
    ):
        raise ValueError("Интеграция требует изолированный PostgreSQL Auth Service")
    return url


@pytest.mark.parametrize(
    "raw_url",
    [
        "postgresql+asyncpg://pdrd:secret@postgres:5432/pdrd",
        "postgresql+asyncpg://auth_test:secret@localhost:5432/pdrd_auth_test",
        "postgresql+asyncpg://auth_test:secret@auth-test-postgres:5432/pdrd",
        "sqlite+aiosqlite:///test.db",
    ],
)
def test_reject_nonisolated_urls(raw_url: str) -> None:
    """Проверка не подключится к рабочему PostgreSQL даже при неверном флаге."""
    with pytest.raises(ValueError, match="изолированный"):
        validate_isolated_database_url(raw_url)


@pytest_asyncio.fixture
async def engine() -> AsyncEngine:
    """Открывает только auth-test-postgres после явного включения интеграции."""
    raw_url = os.environ.get("AUTH_SERVICE_TEST_DATABASE_URL")
    if os.environ.get("PDRD_RUN_DATABASE_TESTS") != "1" or not raw_url:
        pytest.skip("Реальный PostgreSQL требует изолированный тестовый Compose")
    url = validate_isolated_database_url(raw_url)
    database = create_async_engine(url, pool_pre_ping=True)
    try:
        async with database.connect() as connection:
            revision = await connection.scalar(
                text("SELECT version_num FROM auth.alembic_version_auth")
            )
            assert revision == EXPECTED_REVISION
        yield database
    finally:
        await database.dispose()


class FakeUsers:
    """Имитирует внутренний ответ user-service без его PostgreSQL."""

    def __init__(self, user_id: UUID) -> None:
        """Сохраняет единственный активный профиль."""
        self.user_id = user_id
        self.version = 1
        self.status = "active"

    async def get_state(self, user_id: UUID) -> UserAccessState | None:
        """Возвращает статус по точному UUID."""
        if user_id != self.user_id:
            return None
        return UserAccessState(user_id, self.status, self.version)


@pytest.mark.asyncio
async def test_schema_is_owned_by_auth_service(engine: AsyncEngine) -> None:
    """Миграция создаёт только auth.sessions и свою таблицу версий."""
    async with engine.connect() as connection:
        rows = await connection.execute(
            text(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_schema IN ('auth', 'users') ORDER BY 1, 2"
            )
        )
        assert set(rows.all()) == {
            ("auth", "alembic_version_auth"),
            ("auth", "sessions"),
        }


@pytest.mark.asyncio
async def test_issue_resolve_and_logout_persist_only_digest(
    engine: AsyncEngine,
) -> None:
    """Секрет браузера отсутствует в строке БД; logout нельзя обойти продлением."""
    user_id = uuid4()
    store = SqlAlchemySessionStore(build_session_factory(engine))
    users = FakeUsers(user_id)
    now = datetime.now(UTC)
    service = SessionService(store, users, clock=lambda: now)
    issued = await service.issue(user_id)
    try:
        async with build_session_factory(engine)() as database:
            row = await database.scalar(
                select(SessionModel).where(
                    SessionModel.session_id == issued.session.session_id
                )
            )
            assert row is not None
            assert row.token_hash == token_digest(issued.token)
            assert issued.token != row.token_hash
            assert "token" not in SessionModel.__table__.columns
            assert not hasattr(issued.session, "token_hash")

        resolved = await service.resolve(issued.token)
        assert resolved.session_id == issued.session.session_id
        await service.revoke_current(issued.token)
        with pytest.raises(SessionInvalid):
            await service.resolve(issued.token)
        original = await store.find_by_token_hash(token_digest(issued.token))
        assert original is not None
        assert await store.renew_if_active(original, now, 3600) is None
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                delete(SessionModel).where(SessionModel.user_id == user_id)
            )


@pytest.mark.asyncio
async def test_owner_scope_and_revoke_all(engine: AsyncEngine) -> None:
    """Чужой UUID не отзывает сессию, массовый отзыв ограничен владельцем."""
    user_id = uuid4()
    other_id = uuid4()
    store = SqlAlchemySessionStore(build_session_factory(engine))
    users = FakeUsers(user_id)
    service = SessionService(store, users)
    first = await service.issue(user_id)
    second = await service.issue(user_id)
    try:
        assert not await service.revoke_owned(other_id, first.session.session_id)
        assert len(await service.list_user(user_id)) == 2
        assert await service.revoke_owned(user_id, first.session.session_id)
        assert len(await service.list_user(user_id)) == 1
        assert await service.revoke_all(user_id) == 1
        assert await service.list_user(user_id) == ()
        with pytest.raises(SessionInvalid):
            await service.resolve(second.token)
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                delete(SessionModel).where(SessionModel.user_id == user_id)
            )


@pytest.mark.asyncio
async def test_authorization_change_revokes_persisted_session(
    engine: AsyncEngine,
) -> None:
    """Изменение роли в user-service делает сохранённый токен недействительным."""
    user_id = uuid4()
    store = SqlAlchemySessionStore(build_session_factory(engine))
    users = FakeUsers(user_id)
    service = SessionService(store, users)
    issued = await service.issue(user_id)
    try:
        users.version = 2
        with pytest.raises(SessionInvalid):
            await service.resolve(issued.token)
        row = await store.find_by_token_hash(token_digest(issued.token))
        assert row is not None and row.revoked_at is not None
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                delete(SessionModel).where(SessionModel.user_id == user_id)
            )


@pytest.mark.asyncio
async def test_parallel_session_issuance_and_resolution(engine: AsyncEngine) -> None:
    """Нагрузочная проверка уникальности токенов и работы пула без реального AD."""
    user_id = uuid4()
    store = SqlAlchemySessionStore(build_session_factory(engine))
    service = SessionService(store, FakeUsers(user_id))
    try:
        issued = await asyncio.gather(*(service.issue(user_id) for _ in range(64)))
        assert len({item.token for item in issued}) == 64
        assert len({item.session.session_id for item in issued}) == 64

        resolved = await asyncio.gather(
            *(service.resolve(item.token) for item in issued)
        )
        assert {item.session_id for item in resolved} == {
            item.session.session_id for item in issued
        }
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                delete(SessionModel).where(SessionModel.user_id == user_id)
            )
