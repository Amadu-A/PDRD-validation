# services/user-service/tests/integration/test_user_database.py

"""Изолированные интеграционные проверки PostgreSQL User Service."""

import asyncio
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
from pdrd_user_service.application.use_cases.external_accounts import ExternalAccounts
from pdrd_user_service.application.use_cases.review_scope import ReviewScopeAccess
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
EXPECTED_REVISION = "20261005_0002"


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
async def test_external_registration_and_verification_persist_status_and_version(
    test_engine: AsyncEngine,
) -> None:
    """PostgreSQL фиксирует ожидание и однократное повышение версии прав."""
    user_id, subject = uuid4(), uuid4()
    factory = build_session_factory(test_engine)
    accounts = ExternalAccounts(
        lambda: SqlAlchemyUnitOfWork(factory), new_id=lambda: user_id
    )
    try:
        pending = await accounts.register(
            subject=subject,
            display_name="Внешний клиент",
            email="client@example.test",
        )
        async with SqlAlchemyUnitOfWork(factory) as work:
            assert await work.users.get_user(user_id) == pending
            assert (
                await work.users.find_identity("email", "pdrd", str(subject)) == pending
            )
        active = await accounts.verify_email(user_id=user_id, subject=subject)
        repeated = await accounts.verify_email(user_id=user_id, subject=subject)
        assert repeated == active
        assert active.status is UserStatus.ACTIVE
        assert active.authorization_version == pending.authorization_version + 1
        async with SqlAlchemyUnitOfWork(factory) as work:
            assert await work.users.get_user(user_id) == active
    finally:
        await remove_test_users(test_engine, user_id)


@pytest.mark.asyncio
async def test_admin_user_repository_paginates_in_stable_order(
    test_engine: AsyncEngine,
) -> None:
    """PostgreSQL применяет сортировку и offset после отдельного подсчёта."""
    first = replace(
        external_user(user_id=uuid4(), email="first@example.test"),
        created_at=datetime(2050, 1, 1, 0, 0, tzinfo=UTC),
    )
    second = replace(
        external_user(user_id=uuid4(), email="second@example.test"),
        created_at=datetime(2050, 1, 1, 0, 1, tzinfo=UTC),
    )
    factory = build_session_factory(test_engine)
    try:
        async with SqlAlchemyUnitOfWork(factory) as work:
            for user in (first, second):
                await work.users.create_user(
                    user,
                    ExternalIdentity(
                        "test", "pagination", str(user.user_id), user.user_id
                    ),
                )
            await work.commit()
        async with SqlAlchemyUnitOfWork(factory) as work:
            first_page, total = await work.users.list_users(limit=1, offset=0)
            second_page, repeated_total = await work.users.list_users(limit=1, offset=1)
        assert total == repeated_total
        assert total >= 2
        assert first_page == (second,)
        assert second_page == (first,)
    finally:
        await remove_test_users(test_engine, first.user_id, second.user_id)


@pytest.mark.asyncio
async def test_concurrent_external_registration_keeps_one_stable_identity(
    test_engine: AsyncEngine,
) -> None:
    """Двенадцать одновременных регистраций создают только один профиль."""
    subject = uuid4()
    candidate_ids = tuple(uuid4() for _ in range(12))
    factory = build_session_factory(test_engine)

    async def register_once(user_id: UUID) -> UserAccount:
        """Открывает собственную транзакцию для каждого запроса."""
        accounts = ExternalAccounts(
            lambda: SqlAlchemyUnitOfWork(factory), new_id=lambda: user_id
        )
        return await accounts.register(
            subject=subject,
            display_name="Параллельный клиент",
            email="parallel@example.test",
        )

    try:
        results = await asyncio.gather(
            *(register_once(user_id) for user_id in candidate_ids)
        )
        assert len({item.user_id for item in results}) == 1
        assert all(item.status is UserStatus.PENDING_VERIFICATION for item in results)
        async with SqlAlchemyUnitOfWork(factory) as work:
            assert (
                await work.users.find_identity("email", "pdrd", str(subject))
                == results[0]
            )
        async with test_engine.connect() as connection:
            stored = (
                await connection.execute(
                    select(UserModel.user_id).where(
                        UserModel.user_id.in_(candidate_ids)
                    )
                )
            ).all()
        assert stored == [(results[0].user_id,)]
    finally:
        await remove_test_users(test_engine, *candidate_ids)


@pytest.mark.asyncio
async def test_atomic_role_replacement_updates_audit_once(
    test_engine: AsyncEngine,
) -> None:
    """PostgreSQL отзывает старую роль, выдаёт новую и повышает версию один раз."""
    actor, target = corporate_member(user_id=uuid4()), corporate_member(user_id=uuid4())
    old_id, new_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    old = RoleAssignment(
        old_id,
        target.user_id,
        Role.DESIGNER,
        RoleSource.LOCAL,
        RoleScope(ScopeKind.OWN),
        now,
    )
    new = RoleAssignment(
        new_id,
        target.user_id,
        Role.DESIGNER,
        RoleSource.LOCAL,
        RoleScope(ScopeKind.OWN),
        now + timedelta(seconds=1),
    )
    factory = build_session_factory(test_engine)
    try:
        async with SqlAlchemyUnitOfWork(factory) as work:
            for user in (actor, target):
                await work.users.create_user(
                    user,
                    ExternalIdentity(
                        "ad", "test.local", str(user.user_id), user.user_id
                    ),
                )
            await work.commit()
        async with SqlAlchemyUnitOfWork(factory) as work:
            await work.users.add_role_assignment(
                replace(target, authorization_version=2), old, 1, actor.user_id
            )
            await work.commit()
        async with SqlAlchemyUnitOfWork(factory) as work:
            await work.users.replace_worker_roles(
                replace(target, authorization_version=3),
                (old_id,),
                new,
                now + timedelta(seconds=1),
                2,
                actor.user_id,
            )
            await work.commit()
        async with SqlAlchemyUnitOfWork(factory) as work:
            updated = await work.users.get_user(target.user_id)
            assignments = await work.users.list_assignments(target.user_id)
        assert updated is not None and updated.authorization_version == 3
        assert assignments[0].assignment_id == old_id
        assert assignments[0].revoked_at == now + timedelta(seconds=1)
        assert assignments[1].assignment_id == new_id
        assert assignments[1].revoked_at is None
        async with factory() as database:
            events = (
                await database.scalars(
                    select(RoleAssignmentEventModel).where(
                        RoleAssignmentEventModel.assignment_id.in_((old_id, new_id))
                    )
                )
            ).all()
        assert {(item.assignment_id, item.action) for item in events} == {
            (old_id, "assign"),
            (old_id, "revoke"),
            (new_id, "assign"),
        }
        assert {
            item.authorization_version for item in events if item.action == "revoke"
        } == {3}
        with pytest.raises(AuthorizationConflict):
            async with SqlAlchemyUnitOfWork(factory) as work:
                await work.users.replace_worker_roles(
                    replace(target, authorization_version=3),
                    (new_id,),
                    None,
                    now + timedelta(seconds=2),
                    2,
                    actor.user_id,
                )
                await work.commit()
    finally:
        await remove_test_users(test_engine, actor.user_id, target.user_id)


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
async def test_department_membership_cas_controls_cross_user_review_scope(
    test_engine: AsyncEngine,
) -> None:
    """PostgreSQL сохраняет CAS членства, а проверка Review учитывает его отзыв."""
    actor, owner = corporate_member(user_id=uuid4()), corporate_member(user_id=uuid4())
    organization = Organization(uuid4(), "Организация для Review")
    department = Department(uuid4(), organization.organization_id, "Отдел Review")
    now = datetime.now(UTC)
    head = RoleAssignment(
        assignment_id=uuid4(),
        user_id=actor.user_id,
        role=Role.DEPARTMENT_HEAD,
        source=RoleSource.LOCAL,
        scope=RoleScope(
            ScopeKind.DEPARTMENT, organization.organization_id, department.department_id
        ),
        created_at=now - timedelta(seconds=1),
    )
    factory = build_session_factory(test_engine)
    access = ReviewScopeAccess(lambda: SqlAlchemyUnitOfWork(factory), clock=lambda: now)
    try:
        async with SqlAlchemyUnitOfWork(factory) as work:
            for user in (actor, owner):
                await work.users.create_user(
                    user,
                    ExternalIdentity(
                        "ad", "review.test", user.user_id.hex, user.user_id
                    ),
                )
            await work.users.create_organization(organization)
            await work.users.create_department(department)
            await work.users.create_membership(
                Membership(
                    actor.user_id,
                    organization.organization_id,
                    department.department_id,
                )
            )
            await work.users.add_role_assignment(
                replace(actor, authorization_version=2), head, 1, actor.user_id
            )
            await work.commit()

        assert not await access.can_read(
            actor_user_id=actor.user_id, owner_user_id=owner.user_id
        )
        active = Membership(
            owner.user_id, organization.organization_id, department.department_id
        )
        async with SqlAlchemyUnitOfWork(factory) as work:
            await work.users.set_department_membership(
                replace(owner, authorization_version=2),
                active,
                expected_authorization_version=1,
            )
            await work.commit()
        assert await access.can_read(
            actor_user_id=actor.user_id, owner_user_id=owner.user_id
        )

        with pytest.raises(AuthorizationConflict):
            async with SqlAlchemyUnitOfWork(factory) as work:
                await work.users.set_department_membership(
                    replace(owner, authorization_version=2),
                    replace(active, active=False),
                    expected_authorization_version=1,
                )
                await work.commit()
        async with SqlAlchemyUnitOfWork(factory) as work:
            await work.users.set_department_membership(
                replace(owner, authorization_version=3),
                replace(active, active=False),
                expected_authorization_version=2,
            )
            await work.commit()
        assert not await access.can_read(
            actor_user_id=actor.user_id, owner_user_id=owner.user_id
        )
    finally:
        await remove_test_users(
            test_engine,
            actor.user_id,
            owner.user_id,
            organization_ids=(organization.organization_id,),
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


@pytest.mark.asyncio
async def test_local_superuser_bootstrap_lookup_and_admin_promotion(
    test_engine: AsyncEngine,
) -> None:
    """Реальная БД проверяет guard, отсутствие дублей, выдачу админства и аудит."""
    from pdrd_user_service.application.use_cases.local_superuser import LocalSuperusers
    from pdrd_user_service.application.use_cases.replace_role import ReplaceWorkerRole

    factory = build_session_factory(test_engine)

    def unit_of_work():
        """Создаёт независимую транзакцию тестовой БД."""
        return SqlAlchemyUnitOfWork(factory)

    superusers = LocalSuperusers(unit_of_work)
    subject = uuid4()
    admin = await superusers.create(subject=subject, username=f"admin-{subject.hex}")
    target = external_user(user_id=uuid4(), email=f"{uuid4()}@example.test")
    try:
        assert (
            await superusers.create(subject=subject, username=admin.login)
        ).user_id == admin.user_id
        with pytest.raises(BootstrapAlreadyPerformed):
            await superusers.create(subject=uuid4(), username="another-admin")
        async with unit_of_work() as work:
            assert (
                await work.users.find_by_login(admin.login.upper().lower())
            ).user_id == admin.user_id
            await work.users.create_user(
                target, ExternalIdentity("email", "pdrd", str(uuid4()), target.user_id)
            )
            await work.commit()
        promoted = await ReplaceWorkerRole(unit_of_work).replace(
            actor_user_id=admin.user_id,
            target_user_id=target.user_id,
            role=Role.PLATFORM_ADMIN,
            scope=RoleScope(ScopeKind.PLATFORM),
            authorization_version=1,
        )
        assert promoted.roles == (Role.PLATFORM_ADMIN,)
        async with unit_of_work() as work:
            saved = await work.users.get_user(target.user_id)
        assert saved.tier is AccessTier.MEMBER and saved.authorization_version == 2
        async with factory() as database:
            event = await database.scalar(
                select(RoleAssignmentEventModel).where(
                    RoleAssignmentEventModel.assignment_id
                    == promoted.assignments[0].assignment_id
                )
            )
            assert event.actor_user_id == admin.user_id and event.action == "assign"
        with pytest.raises(AuthorizationConflict):
            await ReplaceWorkerRole(unit_of_work).replace(
                actor_user_id=admin.user_id,
                target_user_id=target.user_id,
                role=None,
                scope=None,
                authorization_version=1,
            )
    finally:
        await remove_test_users(test_engine, admin.user_id, target.user_id)
