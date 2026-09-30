# services/user-service/tests/integration/test_user_database.py

"""Изолированные интеграционные проверки PostgreSQL User Service."""

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from pdrd_user_service.application.ports.repository import (
    AuthorizationConflict,
    BootstrapAlreadyPerformed,
    IdentityConflict,
)
from pdrd_user_service.core.settings import DatabaseSettings, Settings
from pdrd_user_service.domain.access import AccessTier, Role
from pdrd_user_service.domain.identity import (
    Department,
    ExternalIdentity,
    Membership,
    Organization,
    UserAccount,
    UserKind,
    UserStatus,
)
from pdrd_user_service.domain.role_assignments import (
    RoleAssignment,
    RoleScope,
    RoleSource,
    ScopeKind,
)
from pdrd_user_service.infrastructure.database.engine import build_session_factory
from pdrd_user_service.infrastructure.database.health import (
    _default_migration_directory,
)
from pdrd_user_service.infrastructure.database.migration_url import (
    resolve_migration_url,
)
from pdrd_user_service.infrastructure.database.models import (
    AdminBootstrapModel,
    DepartmentModel,
    OrganizationModel,
    RoleAssignmentEventModel,
    RoleAssignmentModel,
    UserModel,
)
from pdrd_user_service.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from pydantic import SecretStr
from sqlalchemy import delete, select, text, update
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

pytestmark = pytest.mark.database

TEST_HOST = "user-test-postgres"
TEST_NAME = "pdrd_user_test"
TEST_USER = "user_test"
EXPECTED_REVISION = "20260930_0001"


def validate_isolated_database_url(raw_url: str) -> URL:
    """Отсекает рабочие БД даже при ошибочно включённом флаге тестов."""
    try:
        url = make_url(raw_url)
    except Exception as error:
        raise ValueError("Некорректный URL тестовой БД.") from error
    if (
        url.drivername != "postgresql+asyncpg"
        or url.host != TEST_HOST
        or url.port not in (None, 5432)
        or url.database != TEST_NAME
        or url.username != TEST_USER
        or not url.password
    ):
        raise ValueError("Интеграционные тесты требуют изолированный PostgreSQL users.")
    return url


@pytest.mark.parametrize(
    "raw_url",
    [
        "postgresql+asyncpg://pdrd:secret@postgres:5432/pdrd",
        "postgresql+asyncpg://user_test:secret@localhost:5432/pdrd_user_test",
        "postgresql+asyncpg://user_test:secret@user-test-postgres:5432/pdrd",
        "sqlite+aiosqlite:///test.db",
    ],
)
def test_reject_nonisolated_urls(raw_url: str) -> None:
    """Миграции общей рабочей базы нельзя выдать за тестовую фикстуру."""
    with pytest.raises(ValueError, match="изолированный"):
        validate_isolated_database_url(raw_url)


def test_explicit_migration_does_not_require_api_internal_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Одноразовая миграция использует DB-пароль без включения HTTP API."""
    monkeypatch.delenv("USER_SERVICE_DATABASE_URL", raising=False)
    settings = Settings(
        _env_file=None,
        enabled=False,
        internal_key=SecretStr(""),
        database=DatabaseSettings(
            host=TEST_HOST,
            name=TEST_NAME,
            user=TEST_USER,
            password=SecretStr("test-only"),
        ),
    )
    url = make_url(resolve_migration_url(settings=settings))
    assert (url.host, url.database, url.username) == (TEST_HOST, TEST_NAME, TEST_USER)


def test_migration_rejects_placeholder_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Явная миграция не должна стартовать с примерным паролем."""
    monkeypatch.delenv("USER_SERVICE_DATABASE_URL", raising=False)
    settings = Settings(
        _env_file=None,
        enabled=False,
        database=DatabaseSettings(password=SecretStr("change-me")),
    )
    with pytest.raises(RuntimeError, match="пароль"):
        resolve_migration_url(settings=settings)


def test_readiness_finds_migrations_in_container_workdir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """После установки wheel readiness читает копию Alembic из /app."""
    service_root = Path(__file__).resolve().parents[2]
    monkeypatch.chdir(service_root)
    assert _default_migration_directory() == service_root / "alembic"


@pytest_asyncio.fixture
async def test_engine() -> AsyncEngine:
    """Подключается только к заранее поднятой временной тестовой БД."""
    if os.environ.get("PDRD_RUN_DATABASE_TESTS") != "1":
        pytest.skip("Реальный PostgreSQL требует PDRD_RUN_DATABASE_TESTS=1.")
    raw_url = os.environ.get("USER_SERVICE_TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Не задан USER_SERVICE_TEST_DATABASE_URL.")
    url = validate_isolated_database_url(raw_url)
    expected_parts = {
        "USER_SERVICE_DATABASE__HOST": TEST_HOST,
        "USER_SERVICE_DATABASE__NAME": TEST_NAME,
        "USER_SERVICE_DATABASE__USER": TEST_USER,
        "USER_SERVICE_DATABASE__PASSWORD": url.password,
    }
    if any(os.environ.get(key) != value for key, value in expected_parts.items()):
        raise ValueError("Миграция и тесты должны использовать одну тестовую БД.")
    if os.environ.get("USER_SERVICE_DATABASE__PORT", "5432") != "5432":
        raise ValueError("Порт миграции и тестовой БД не совпадает.")

    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            identity = (
                await connection.execute(
                    text("SELECT current_database(), current_user")
                )
            ).one()
            assert identity == (TEST_NAME, TEST_USER)
            revision = await connection.scalar(
                text("SELECT version_num FROM users.alembic_version_users")
            )
            assert revision == EXPECTED_REVISION
        yield engine
    finally:
        await engine.dispose()


def external_user(*, user_id: UUID, email: str) -> UserAccount:
    """Создаёт внешний профиль с бесплатным уровнем доступа."""
    return UserAccount(
        user_id=user_id,
        kind=UserKind.EXTERNAL,
        tier=AccessTier.REGISTERED_FREE,
        status=UserStatus.ACTIVE,
        display_name="Тестовый пользователь",
        email=email,
        created_at=datetime.now(UTC),
    )


def corporate_member(*, user_id: UUID) -> UserAccount:
    """Создаёт активного сотрудника с рабочим уровнем доступа."""
    return UserAccount(
        user_id=user_id,
        kind=UserKind.CORPORATE,
        tier=AccessTier.MEMBER,
        status=UserStatus.ACTIVE,
        display_name="Сотрудник",
        login=f"test-{user_id.hex}",
        created_at=datetime.now(UTC),
    )


async def remove_test_users(
    engine: AsyncEngine, *user_ids: UUID, organization_ids: tuple[UUID, ...] = ()
) -> None:
    """Удаляет лишь собственные тестовые строки, без TRUNCATE и DROP."""
    async with engine.begin() as connection:
        await connection.execute(
            delete(RoleAssignmentEventModel).where(
                RoleAssignmentEventModel.assignment_id.in_(
                    select(RoleAssignmentModel.assignment_id).where(
                        RoleAssignmentModel.user_id.in_(user_ids)
                    )
                )
            )
        )
        await connection.execute(
            delete(AdminBootstrapModel).where(AdminBootstrapModel.user_id.in_(user_ids))
        )
        await connection.execute(
            delete(UserModel).where(UserModel.user_id.in_(user_ids))
        )
        if organization_ids:
            await connection.execute(
                delete(DepartmentModel).where(
                    DepartmentModel.organization_id.in_(organization_ids)
                )
            )
            await connection.execute(
                delete(OrganizationModel).where(
                    OrganizationModel.organization_id.in_(organization_ids)
                )
            )


@pytest.mark.asyncio
async def test_stable_identity_is_unique_but_same_email_is_not(
    test_engine: AsyncEngine,
) -> None:
    """Два провайдера могут иметь общий email, но один stable key не дублируется."""
    user_a = external_user(user_id=uuid4(), email="shared@example.test")
    user_b = external_user(user_id=uuid4(), email="shared@example.test")
    ghost_id = uuid4()
    factory = build_session_factory(test_engine)
    try:
        async with SqlAlchemyUnitOfWork(factory) as work:
            await work.users.create_user(
                user_a, ExternalIdentity("local", "pdrd", "subject-a", user_a.user_id)
            )
            await work.users.create_user(
                user_b, ExternalIdentity("other", "tenant", "subject-b", user_b.user_id)
            )
            await work.commit()

        async with SqlAlchemyUnitOfWork(factory) as work:
            assert (
                await work.users.find_identity("local", "pdrd", "subject-a") == user_a
            )
            assert (
                await work.users.find_identity("other", "tenant", "subject-b") == user_b
            )

        with pytest.raises(IdentityConflict):
            async with SqlAlchemyUnitOfWork(factory) as work:
                await work.users.create_user(
                    external_user(user_id=ghost_id, email="ghost@example.test"),
                    ExternalIdentity("local", "pdrd", "subject-a", ghost_id),
                )
                await work.commit()
        async with SqlAlchemyUnitOfWork(factory) as work:
            assert await work.users.get_user(ghost_id) is None
    finally:
        await remove_test_users(test_engine, user_a.user_id, user_b.user_id, ghost_id)


@pytest.mark.asyncio
async def test_multiple_departments_and_disabled_tenant_remove_effective_membership(
    test_engine: AsyncEngine,
) -> None:
    """Несколько отделов допустимы; отключённая граница сразу отнимает права."""
    user = corporate_member(user_id=uuid4())
    organization = Organization(uuid4(), "Тестовая организация")
    other_organization = Organization(uuid4(), "Чужая организация")
    first = Department(uuid4(), organization.organization_id, "Первый отдел")
    second = Department(uuid4(), organization.organization_id, "Второй отдел")
    factory = build_session_factory(test_engine)
    try:
        async with SqlAlchemyUnitOfWork(factory) as work:
            await work.users.create_user(
                user,
                ExternalIdentity("ad", "test.local", user.user_id.hex, user.user_id),
            )
            await work.users.create_organization(organization)
            await work.users.create_organization(other_organization)
            await work.users.create_department(first)
            await work.users.create_department(second)
            for department in (first, second):
                await work.users.create_membership(
                    Membership(
                        user.user_id,
                        organization.organization_id,
                        department.department_id,
                    )
                )
            await work.commit()

        async with SqlAlchemyUnitOfWork(factory) as work:
            memberships = await work.users.list_memberships(user.user_id)
        assert {item.department_id for item in memberships} == {
            first.department_id,
            second.department_id,
        }
        assert all(item.active for item in memberships)

        with pytest.raises(IntegrityError):
            async with SqlAlchemyUnitOfWork(factory) as work:
                await work.users.create_membership(
                    Membership(
                        user.user_id,
                        other_organization.organization_id,
                        first.department_id,
                    )
                )
                await work.commit()

        async with test_engine.begin() as connection:
            await connection.execute(
                update(DepartmentModel)
                .where(DepartmentModel.department_id == first.department_id)
                .values(active=False)
            )
        async with SqlAlchemyUnitOfWork(factory) as work:
            memberships = await work.users.list_memberships(user.user_id)
        active = {item.department_id: item.active for item in memberships}
        assert active == {first.department_id: False, second.department_id: True}

        async with test_engine.begin() as connection:
            await connection.execute(
                update(OrganizationModel)
                .where(
                    OrganizationModel.organization_id == organization.organization_id
                )
                .values(active=False)
            )
        async with SqlAlchemyUnitOfWork(factory) as work:
            assert not any(
                item.active for item in await work.users.list_memberships(user.user_id)
            )
    finally:
        await remove_test_users(
            test_engine,
            user.user_id,
            organization_ids=(
                organization.organization_id,
                other_organization.organization_id,
            ),
        )


@pytest.mark.asyncio
async def test_role_version_and_one_time_admin_guard_are_atomic(
    test_engine: AsyncEngine,
) -> None:
    """Устаревшая версия и неудачный bootstrap не оставляют частичных записей."""
    user = corporate_member(user_id=uuid4())
    other = corporate_member(user_id=uuid4())
    factory = build_session_factory(test_engine)
    now = datetime.now(UTC)
    designer_id = uuid4()
    designer = RoleAssignment(
        assignment_id=designer_id,
        user_id=user.user_id,
        role=Role.DESIGNER,
        source=RoleSource.LOCAL,
        scope=RoleScope(ScopeKind.OWN),
        created_at=now,
    )
    try:
        async with SqlAlchemyUnitOfWork(factory) as work:
            for account in (user, other):
                await work.users.create_user(
                    account,
                    ExternalIdentity(
                        "ad", "test.local", account.user_id.hex, account.user_id
                    ),
                )
            await work.commit()
        async with SqlAlchemyUnitOfWork(factory) as work:
            await work.users.add_role_assignment(
                replace(user, authorization_version=2), designer, 1, user.user_id
            )
            await work.commit()

        stale = replace(
            designer, assignment_id=uuid4(), created_at=now + timedelta(seconds=1)
        )
        with pytest.raises(AuthorizationConflict):
            async with SqlAlchemyUnitOfWork(factory) as work:
                await work.users.add_role_assignment(
                    replace(user, authorization_version=2), stale, 1, user.user_id
                )
                await work.commit()
        async with SqlAlchemyUnitOfWork(factory) as work:
            assert (await work.users.get_user(user.user_id)).authorization_version == 2
            assert len(await work.users.list_assignments(user.user_id)) == 1

        duplicate_admin = RoleAssignment(
            assignment_id=designer_id,
            user_id=user.user_id,
            role=Role.PLATFORM_ADMIN,
            source=RoleSource.LOCAL,
            scope=RoleScope(ScopeKind.PLATFORM),
            created_at=now + timedelta(seconds=2),
        )
        with pytest.raises(BootstrapAlreadyPerformed):
            async with SqlAlchemyUnitOfWork(factory) as work:
                await work.users.bootstrap_admin(
                    replace(user, authorization_version=3), duplicate_admin, 2
                )
                await work.commit()
        async with test_engine.connect() as connection:
            assert (
                await connection.scalar(select(AdminBootstrapModel.singleton_id))
                is None
            )
            assert (
                await connection.scalar(
                    select(UserModel.authorization_version).where(
                        UserModel.user_id == user.user_id
                    )
                )
                == 2
            )

        admin = replace(duplicate_admin, assignment_id=uuid4())
        async with SqlAlchemyUnitOfWork(factory) as work:
            await work.users.bootstrap_admin(
                replace(user, authorization_version=3), admin, 2
            )
            await work.commit()
        second_admin = replace(admin, user_id=other.user_id, assignment_id=uuid4())
        with pytest.raises(BootstrapAlreadyPerformed):
            async with SqlAlchemyUnitOfWork(factory) as work:
                await work.users.bootstrap_admin(
                    replace(other, authorization_version=2), second_admin, 1
                )
                await work.commit()
        async with SqlAlchemyUnitOfWork(factory) as work:
            await work.users.revoke_role_assignment(
                replace(user, authorization_version=4),
                designer_id,
                now + timedelta(seconds=3),
                3,
                user.user_id,
            )
            await work.commit()
        async with SqlAlchemyUnitOfWork(factory) as work:
            assert (await work.users.get_user(user.user_id)).authorization_version == 4
            assert (await work.users.get_user(other.user_id)).authorization_version == 1
        async with test_engine.connect() as connection:
            events = (
                await connection.execute(
                    select(
                        RoleAssignmentEventModel.assignment_id,
                        RoleAssignmentEventModel.action,
                        RoleAssignmentEventModel.actor_user_id,
                        RoleAssignmentEventModel.authorization_version,
                    ).where(
                        RoleAssignmentEventModel.assignment_id.in_(
                            (designer_id, admin.assignment_id)
                        )
                    )
                )
            ).all()
        assert set(events) == {
            (designer_id, "assign", user.user_id, 2),
            (admin.assignment_id, "bootstrap", None, 3),
            (designer_id, "revoke", user.user_id, 4),
        }
    finally:
        await remove_test_users(test_engine, user.user_id, other.user_id)
