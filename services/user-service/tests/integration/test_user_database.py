# services/user-service/tests/integration/test_user_database.py

"""Изолированные интеграционные проверки PostgreSQL User Service."""

import asyncio
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from pdrd_auth_service.application.ports.sessions import UserAccessState
from pdrd_auth_service.application.use_cases.sessions import (
    SessionInvalid,
    SessionService,
)
from pdrd_user_service.application.ports.repository import (
    AuthorizationConflict,
    BootstrapAlreadyPerformed,
    IdentityConflict,
)
from pdrd_user_service.application.use_cases.distribute_section import DistributeSection
from pdrd_user_service.application.use_cases.external_accounts import ExternalAccounts
from pdrd_user_service.application.use_cases.review_access import ReviewAccessManagement
from pdrd_user_service.application.use_cases.review_scope import ReviewScopeAccess
from pdrd_user_service.application.use_cases.users import UserDirectory
from pdrd_user_service.core.settings import DatabaseSettings, Settings
from pdrd_user_service.domain.access import AccessTier, Permission, Role
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
from pdrd_user_service.infrastructure.database.base import Base
from pdrd_user_service.infrastructure.database.engine import build_session_factory
from pdrd_user_service.infrastructure.database.health import (
    _default_migration_directory,
)
from pdrd_user_service.infrastructure.database.migration_url import (
    resolve_migration_url,
)
from pdrd_user_service.infrastructure.database.models import (
    AdminBootstrapModel,
    CatalogSectionDistributionModel,
    DepartmentModel,
    OrganizationModel,
    ReviewAccessEventModel,
    RoleAssignmentEventModel,
    RoleAssignmentModel,
    SectionAccessEventModel,
    UserModel,
)
from pdrd_user_service.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from pydantic import SecretStr
from sqlalchemy import delete, inspect, select, text, update
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

pytestmark = pytest.mark.database

TEST_HOST = "user-test-postgres"
TEST_NAME = "pdrd_user_test"
TEST_USER = "user_test"
EXPECTED_REVISION = "20261006_0005"


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
            delete(SectionAccessEventModel).where(
                SectionAccessEventModel.user_id.in_(user_ids)
            )
        )
        await connection.execute(
            delete(CatalogSectionDistributionModel).where(
                CatalogSectionDistributionModel.actor_user_id.in_(user_ids)
            )
        )
        await connection.execute(
            delete(ReviewAccessEventModel).where(
                ReviewAccessEventModel.user_id.in_(user_ids)
            )
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


class SectionCatalog:
    """Выдаёт конкретные разделы изолированному PostgreSQL сценарию."""

    def __init__(self, section_ids: tuple[UUID, ...] = ()) -> None:
        """Хранит внешний снимок без FK к Knowledge Service."""
        self.section_ids = section_ids

    async def list_section_ids(self) -> tuple[UUID, ...]:
        """Возвращает исходные UUID при первой активации."""
        return self.section_ids


@pytest.mark.asyncio
async def test_external_registration_and_verification_persist_status_and_version(
    test_engine: AsyncEngine,
) -> None:
    """PostgreSQL фиксирует ожидание и однократное повышение версии прав."""
    user_id, subject = uuid4(), uuid4()
    factory = build_session_factory(test_engine)
    accounts = ExternalAccounts(
        lambda: SqlAlchemyUnitOfWork(factory),
        section_catalog=SectionCatalog(),
        new_id=lambda: user_id,
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
        assert active.tier is AccessTier.MEMBER
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


@pytest.mark.asyncio
async def test_multiple_section_grants_roundtrip_and_rollback(
    test_engine: AsyncEngine,
) -> None:
    """Реальная БД сохраняет UUID и аудит вместе с версией; ошибка откатывает всё."""
    admin_id, user_id, section_a, section_b = (uuid4() for _ in range(4))
    sessions = build_session_factory(test_engine)
    now = datetime.now(UTC)
    user = corporate_member(user_id=user_id)
    try:
        async with SqlAlchemyUnitOfWork(sessions) as work:
            await work.users.create_user(
                corporate_member(user_id=admin_id),
                ExternalIdentity("ad", "test", admin_id.hex, admin_id),
            )
            await work.users.create_user(
                user, ExternalIdentity("ad", "test", user_id.hex, user_id)
            )
            await work.commit()
        async with SqlAlchemyUnitOfWork(sessions) as work:
            await work.users.replace_sections(
                user_id,
                (section_a, section_b),
                actor_user_id=admin_id,
                authorization_version=2,
                created_at=now,
            )
            await work.users.replace_worker_roles(
                updated_user=replace(user, authorization_version=2),
                previous_assignment_ids=(),
                new_assignment=None,
                revoked_at=now,
                expected_authorization_version=1,
                actor_user_id=admin_id,
            )
            await work.commit()
        async with SqlAlchemyUnitOfWork(sessions) as work:
            assert set(await work.users.list_sections(user_id)) == {
                section_a,
                section_b,
            }
            assert (await work.users.get_user(user_id)).authorization_version == 2
        with pytest.raises(RuntimeError, match="откат"):
            async with SqlAlchemyUnitOfWork(sessions) as work:
                await work.users.replace_sections(
                    user_id,
                    (),
                    actor_user_id=admin_id,
                    authorization_version=3,
                    created_at=now,
                )
                await work.users.replace_worker_roles(
                    updated_user=replace(user, authorization_version=3),
                    previous_assignment_ids=(),
                    new_assignment=None,
                    revoked_at=now,
                    expected_authorization_version=2,
                    actor_user_id=admin_id,
                )
                raise RuntimeError("откат")
        async with SqlAlchemyUnitOfWork(sessions) as work:
            assert set(await work.users.list_sections(user_id)) == {
                section_a,
                section_b,
            }
            assert (await work.users.get_user(user_id)).authorization_version == 2
        async with test_engine.connect() as connection:
            events = (
                (
                    await connection.execute(
                        select(SectionAccessEventModel.authorization_version).where(
                            SectionAccessEventModel.user_id == user_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert events == [2]
    finally:
        await remove_test_users(test_engine, user_id, admin_id)


@pytest.mark.asyncio
async def test_concurrent_ad_provisioning_assigns_default_access_once(
    test_engine: AsyncEngine,
) -> None:
    """Параллельный первый вход даёт один профиль, designer, разделы и аудит v2."""
    subject = uuid4().hex
    section_ids = (uuid4(), uuid4())
    candidate_ids = tuple(uuid4() for _ in range(8))
    factory = build_session_factory(test_engine)

    async def first_login(user_id: UUID) -> UserAccount:
        """Открывает отдельную транзакцию первого подтверждённого AD-входа."""
        directory = UserDirectory(
            lambda: SqlAlchemyUnitOfWork(factory),
            section_catalog=SectionCatalog(section_ids),
            new_id=lambda: user_id,
        )
        return await directory.provision(
            provider_id="ad",
            namespace="onboarding-test",
            subject=subject,
            kind=UserKind.CORPORATE,
            display_name="Новый сотрудник",
            login=f"new-{subject}",
        )

    try:
        users = await asyncio.gather(
            *(first_login(user_id) for user_id in candidate_ids)
        )
        assert len({user.user_id for user in users}) == 1
        assert all(user.authorization_version == 2 for user in users)
        user_id = users[0].user_id
        async with SqlAlchemyUnitOfWork(factory) as work:
            assignments = await work.users.list_assignments(user_id)
            assert len(assignments) == 1
            assert assignments[0].role is Role.DESIGNER
            assert set(await work.users.list_sections(user_id)) == set(section_ids)
        async with factory() as session:
            role_versions = tuple(
                await session.scalars(
                    select(RoleAssignmentEventModel.authorization_version).where(
                        RoleAssignmentEventModel.assignment_id
                        == assignments[0].assignment_id
                    )
                )
            )
            section_versions = tuple(
                await session.scalars(
                    select(SectionAccessEventModel.authorization_version).where(
                        SectionAccessEventModel.user_id == user_id
                    )
                )
            )
            assert role_versions == section_versions == (2,)
    finally:
        await remove_test_users(test_engine, *candidate_ids)


@pytest.mark.asyncio
async def test_email_confirmation_initializes_role_sections_and_single_version(
    test_engine: AsyncEngine,
) -> None:
    """Подтверждение email атомарно сохраняет MEMBER, designer и все UUID разделов."""
    user_id, subject = uuid4(), uuid4()
    section_ids = (uuid4(), uuid4())
    factory = build_session_factory(test_engine)
    accounts = ExternalAccounts(
        lambda: SqlAlchemyUnitOfWork(factory),
        section_catalog=SectionCatalog(section_ids),
        new_id=lambda: user_id,
    )
    try:
        pending = await accounts.register(
            subject=subject,
            display_name="Новый клиент",
            email=f"{subject.hex}@example.test",
        )
        async with SqlAlchemyUnitOfWork(factory) as work:
            assert pending.tier is AccessTier.REGISTERED_FREE
            assert await work.users.list_assignments(user_id) == ()
            assert await work.users.list_sections(user_id) == ()
        active, repeated = await asyncio.gather(
            accounts.verify_email(user_id=user_id, subject=subject),
            accounts.verify_email(user_id=user_id, subject=subject),
        )
        assert active == repeated
        assert active.authorization_version == 2
        async with SqlAlchemyUnitOfWork(factory) as work:
            saved = await work.users.get_user(user_id)
            assert saved == active
            assert saved.tier is AccessTier.MEMBER
            assert len(await work.users.list_assignments(user_id)) == 1
            assert set(await work.users.list_sections(user_id)) == set(section_ids)
        async with factory() as session:
            events = tuple(
                await session.scalars(
                    select(SectionAccessEventModel.authorization_version).where(
                        SectionAccessEventModel.user_id == user_id
                    )
                )
            )
            assert events == (2,)
    finally:
        await remove_test_users(test_engine, user_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("email_confirmation", [False, True])
async def test_initial_access_failure_rolls_back_all_records(
    test_engine: AsyncEngine, email_confirmation: bool
) -> None:
    """Сбой перед commit откатывает профиль/активацию, роль, гранты и оба аудита."""
    user_id, subject = uuid4(), uuid4()
    factory = build_session_factory(test_engine)
    catalog = SectionCatalog((uuid4(), uuid4()))

    class FailingCommit(SqlAlchemyUnitOfWork):
        """Останавливает транзакцию после сохранения всех новых записей."""

        async def commit(self) -> None:
            """Не допускает commit, чтобы проверить реальный rollback PostgreSQL."""
            raise RuntimeError("Сбой после сохранения доступа")

    try:
        if email_confirmation:
            accounts = ExternalAccounts(
                lambda: SqlAlchemyUnitOfWork(factory),
                section_catalog=catalog,
                new_id=lambda: user_id,
            )
            pending = await accounts.register(
                subject=subject,
                display_name="Клиент",
                email=f"{subject.hex}@example.test",
            )
            failed = ExternalAccounts(
                lambda: FailingCommit(factory), section_catalog=catalog
            )
            with pytest.raises(RuntimeError, match="после сохранения"):
                await failed.verify_email(user_id=user_id, subject=subject)
        else:
            directory = UserDirectory(
                lambda: FailingCommit(factory),
                section_catalog=catalog,
                new_id=lambda: user_id,
            )
            with pytest.raises(RuntimeError, match="после сохранения"):
                await directory.provision(
                    provider_id="ad",
                    namespace="rollback-test",
                    subject=str(subject),
                    kind=UserKind.CORPORATE,
                    display_name="Сотрудник",
                    login=f"rollback-{subject.hex}",
                )
        async with SqlAlchemyUnitOfWork(factory) as work:
            saved = await work.users.get_user(user_id)
            if email_confirmation:
                assert saved == pending
            else:
                assert saved is None
                assert (
                    await work.users.find_identity("ad", "rollback-test", str(subject))
                    is None
                )
            assert await work.users.list_assignments(user_id) == ()
            assert await work.users.list_sections(user_id) == ()
        async with factory() as session:
            assert (
                tuple(
                    await session.scalars(
                        select(SectionAccessEventModel.event_id).where(
                            SectionAccessEventModel.user_id == user_id
                        )
                    )
                )
                == ()
            )
            assert (
                tuple(
                    await session.scalars(
                        select(RoleAssignmentEventModel.event_id).where(
                            RoleAssignmentEventModel.actor_user_id == user_id
                        )
                    )
                )
                == ()
            )
    finally:
        await remove_test_users(test_engine, user_id)


async def create_catalog_worker(
    factory: object, user_id: UUID, role: Role
) -> UserAccount:
    """Создаёт только собственную тестовую рабочую роль с итоговой версией 2."""
    profile = corporate_member(user_id=user_id)
    updated = replace(profile, authorization_version=2)
    scope = ScopeKind.SECTIONS if role is Role.DEPARTMENT_HEAD else ScopeKind.OWN
    assignment = RoleAssignment(
        assignment_id=uuid4(),
        user_id=user_id,
        role=role,
        source=RoleSource.LOCAL,
        scope=RoleScope(scope),
        created_at=datetime.now(UTC),
    )
    async with SqlAlchemyUnitOfWork(factory) as work:
        await work.users.create_user(
            profile, ExternalIdentity("test", "catalog-worker", user_id.hex, user_id)
        )
        await work.users.add_role_assignment(updated, assignment, 1, user_id)
        await work.commit()
    return updated


@pytest.mark.asyncio
async def test_concurrent_section_distribution_preserves_sessions_and_manual_revocations(
    test_engine: AsyncEngine,
) -> None:
    """PG marker выдаёт раздел один раз, не меняя версии и не возвращая ручной отзыв."""
    head_id, target_id, new_section, old_section = (uuid4() for _ in range(4))
    factory = build_session_factory(test_engine)
    try:
        await create_catalog_worker(factory, head_id, Role.DEPARTMENT_HEAD)
        target = await create_catalog_worker(factory, target_id, Role.DESIGNER)
        async with SqlAlchemyUnitOfWork(factory) as work:
            for user_id in (head_id, target_id):
                await work.users.replace_sections(
                    user_id,
                    (old_section,),
                    actor_user_id=head_id,
                    authorization_version=2,
                    created_at=datetime.now(UTC),
                )
            await work.commit()
        service = DistributeSection(
            lambda: SqlAlchemyUnitOfWork(factory), SectionCatalog((new_section,))
        )
        results = await asyncio.gather(
            *(
                service.execute(section_id=new_section, actor_user_id=head_id)
                for _ in range(4)
            )
        )
        assert sum(result.granted_users for result in results) == 2
        assert sum(not result.already_distributed for result in results) == 1
        async with SqlAlchemyUnitOfWork(factory) as work:
            for user_id in (head_id, target_id):
                assert (await work.users.get_user(user_id)).authorization_version == 2
                assert set(await work.users.list_sections(user_id)) == {
                    new_section,
                    old_section,
                }
            # Ручная CAS-правка оставляет роль, но снимает новый раздел с новой версией.
            changed = replace(target, authorization_version=3)
            await work.users.replace_worker_roles(
                changed, (), None, datetime.now(UTC), 2, head_id
            )
            await work.users.replace_sections(
                target_id,
                (old_section,),
                actor_user_id=head_id,
                authorization_version=3,
                created_at=datetime.now(UTC),
            )
            await work.commit()
        repeated = await service.execute(section_id=new_section, actor_user_id=head_id)
        assert repeated.already_distributed is True
        assert repeated.granted_users == 0
        async with SqlAlchemyUnitOfWork(factory) as work:
            assert await work.users.list_sections(target_id) == (old_section,)
            assert (await work.users.get_user(target_id)).authorization_version == 3
        async with factory() as session:
            assert tuple(
                await session.scalars(
                    select(CatalogSectionDistributionModel.section_id).where(
                        CatalogSectionDistributionModel.section_id == new_section
                    )
                )
            ) == (new_section,)
    finally:
        await remove_test_users(test_engine, head_id, target_id)


@pytest.mark.asyncio
async def test_distribution_failure_rolls_back_marker_and_grants_in_postgresql(
    test_engine: AsyncEngine,
) -> None:
    """Настоящий rollback позволяет повторить целую выдачу после сбоя commit."""
    head_id, target_id, new_section = (uuid4() for _ in range(3))
    factory = build_session_factory(test_engine)

    class FailingCommit(SqlAlchemyUnitOfWork):
        """Имитирует отказ после записи marker и всех назначений."""

        async def commit(self) -> None:
            """Оставляет внешнему контексту rollback незавершённой транзакции."""
            raise RuntimeError("Сбой фиксации выдачи")

    try:
        await create_catalog_worker(factory, head_id, Role.DEPARTMENT_HEAD)
        await create_catalog_worker(factory, target_id, Role.DESIGNER)
        catalog = SectionCatalog((new_section,))
        service = DistributeSection(lambda: FailingCommit(factory), catalog)
        with pytest.raises(RuntimeError, match="фиксации выдачи"):
            await service.execute(section_id=new_section, actor_user_id=head_id)
        async with SqlAlchemyUnitOfWork(factory) as work:
            assert await work.users.list_sections(head_id) == ()
            assert await work.users.list_sections(target_id) == ()
            assert new_section not in await work.users.list_distributed_sections()
        service = DistributeSection(lambda: SqlAlchemyUnitOfWork(factory), catalog)
        assert (
            await service.execute(section_id=new_section, actor_user_id=head_id)
        ).granted_users == 2
    finally:
        await remove_test_users(test_engine, head_id, target_id)


@pytest.mark.asyncio
async def test_first_login_snapshot_race_keeps_newly_distributed_section(
    test_engine: AsyncEngine,
) -> None:
    """Advisory lock и marker закрывают разрыв между HTTP-снимком и созданием профиля."""
    head_id, target_id, old_section, new_section = (uuid4() for _ in range(4))
    factory = build_session_factory(test_engine)
    snapshot_started, release_snapshot = asyncio.Event(), asyncio.Event()

    class SnapshotCatalog:
        """Останавливает внешний снимок, пока другой сценарий создаёт новый раздел."""

        async def list_section_ids(self) -> tuple[UUID, ...]:
            """Возвращает прежний каталог после завершения конкурентной выдачи."""
            snapshot_started.set()
            await release_snapshot.wait()
            return (old_section,)

    task = None
    try:
        await create_catalog_worker(factory, head_id, Role.DEPARTMENT_HEAD)
        directory = UserDirectory(
            lambda: SqlAlchemyUnitOfWork(factory),
            section_catalog=SnapshotCatalog(),
            new_id=lambda: target_id,
        )
        task = asyncio.create_task(
            directory.provision(
                provider_id="ad",
                namespace="catalog-race",
                subject=target_id.hex,
                kind=UserKind.CORPORATE,
                display_name="Новый сотрудник",
                login=f"race-{target_id.hex}",
            )
        )
        await asyncio.wait_for(snapshot_started.wait(), 10)
        service = DistributeSection(
            lambda: SqlAlchemyUnitOfWork(factory), SectionCatalog((new_section,))
        )
        result = await service.execute(section_id=new_section, actor_user_id=head_id)
        assert result.granted_users == 1
        release_snapshot.set()
        user = await asyncio.wait_for(task, 10)
        assert user.authorization_version == 2
        async with SqlAlchemyUnitOfWork(factory) as work:
            assert set(await work.users.list_sections(target_id)) == {
                old_section,
                new_section,
            }
    finally:
        release_snapshot.set()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await remove_test_users(test_engine, head_id, target_id)


async def create_review_test_users(factory, admin_id, target_id):
    """Создаёт администратора и проектировщика с исходной версией прав 2."""
    admin = corporate_member(user_id=admin_id)
    assignment = RoleAssignment(
        assignment_id=uuid4(),
        user_id=admin_id,
        role=Role.PLATFORM_ADMIN,
        source=RoleSource.LOCAL,
        scope=RoleScope(ScopeKind.PLATFORM),
        created_at=datetime.now(UTC),
    )
    async with SqlAlchemyUnitOfWork(factory) as work:
        await work.users.create_user(
            admin, ExternalIdentity("test", "review-admin", admin_id.hex, admin_id)
        )
        await work.users.bootstrap_admin(
            replace(admin, authorization_version=2), assignment, 1
        )
        await work.commit()
    return await create_catalog_worker(factory, target_id, Role.DESIGNER)


@pytest.mark.asyncio
async def test_concurrent_review_grant_and_revoke_keep_single_audit_and_live_permissions(
    test_engine,
):
    """Конкурентная выдача выигрывает один CAS; отзыв убирает права и меняет версию сессий."""
    admin_id, target_id = uuid4(), uuid4()
    factory = build_session_factory(test_engine)
    try:
        await create_review_test_users(factory, admin_id, target_id)
        management = ReviewAccessManagement(lambda: SqlAlchemyUnitOfWork(factory))
        directory = UserDirectory(lambda: SqlAlchemyUnitOfWork(factory))
        store, profiles = AsyncMock(), AsyncMock()
        rows = {}
        store.create.side_effect = lambda session: rows.setdefault(
            session.token_hash, session
        )
        store.find_by_token_hash.side_effect = rows.get
        store.renew_if_active.side_effect = lambda session, now, idle: session

        async def live_state(user_id):
            """Читает живую версию User Service для настоящего сценария Auth."""
            async with SqlAlchemyUnitOfWork(factory) as work:
                user = await work.users.get_user(user_id)
            return UserAccessState(
                user.user_id, user.status.value, user.authorization_version
            )

        profiles.get_state.side_effect = live_state
        sessions = SessionService(store, profiles)
        old_session = await sessions.issue(target_id)
        await sessions.resolve(old_session.token)
        before = await directory.permissions(target_id)
        assert Permission.ANALYSIS_RUN in before.permissions
        assert Permission.REVIEW_GOLD_CREATE not in before.permissions

        async def grant():
            """Пытается выдать доступ по одной и той же исходной версии."""
            return await management.change(
                actor_user_id=admin_id,
                target_user_id=target_id,
                enabled=True,
                authorization_version=2,
            )

        results = await asyncio.gather(
            *(grant() for _ in range(6)), return_exceptions=True
        )
        assert sum(not isinstance(item, Exception) for item in results) == 1
        assert sum(isinstance(item, AuthorizationConflict) for item in results) == 5
        with pytest.raises(SessionInvalid):
            await sessions.resolve(old_session.token)
        granted_session = await sessions.issue(target_id)
        await sessions.resolve(granted_session.token)
        granted = await directory.permissions(target_id)
        assert granted.authorization_version == 3
        assert {
            Permission.REVIEW_GOLD_CREATE,
            Permission.REVIEW_FINDINGS_DECIDE,
            Permission.REVIEW_APPROVE,
            Permission.REVIEWED_PDF_DOWNLOAD,
            Permission.EXPERIENCE_CAPTURE,
        } <= set(granted.permissions)
        assert Permission.REVIEW_SCOPED_READ not in granted.permissions
        repeated = await management.change(
            actor_user_id=admin_id,
            target_user_id=target_id,
            enabled=True,
            authorization_version=3,
        )
        assert repeated.user.authorization_version == 3
        await management.change(
            actor_user_id=admin_id,
            target_user_id=target_id,
            enabled=False,
            authorization_version=3,
        )
        with pytest.raises(SessionInvalid):
            await sessions.resolve(granted_session.token)
        assert store.revoke_token_hash.await_count == 2
        revoked = await directory.permissions(target_id)
        assert revoked.authorization_version == 4
        assert revoked.authorization_version != granted.authorization_version
        assert Permission.REVIEW_GOLD_CREATE not in revoked.permissions
        assert Permission.EXPERIENCE_CAPTURE not in revoked.permissions
        assert Permission.ANALYSIS_RUN in revoked.permissions
        async with test_engine.connect() as connection:
            events = (
                await connection.execute(
                    select(
                        ReviewAccessEventModel.actor_user_id,
                        ReviewAccessEventModel.enabled,
                        ReviewAccessEventModel.authorization_version,
                    )
                    .where(ReviewAccessEventModel.user_id == target_id)
                    .order_by(ReviewAccessEventModel.authorization_version)
                )
            ).all()
        assert events == [(admin_id, True, 3), (admin_id, False, 4)]
    finally:
        await remove_test_users(test_engine, admin_id, target_id)


@pytest.mark.asyncio
async def test_review_flag_version_and_audit_roll_back_together(test_engine):
    """Откат и невалидный актёр не оставляют флаг или новую версию без аудита."""
    target_id = uuid4()
    factory = build_session_factory(test_engine)
    try:
        profile = await create_catalog_worker(factory, target_id, Role.DESIGNER)
        updated = replace(profile, review_access_enabled=True, authorization_version=3)
        async with SqlAlchemyUnitOfWork(factory) as work:
            await work.users.replace_review_access(
                updated,
                expected_authorization_version=2,
                actor_user_id=target_id,
                created_at=datetime.now(UTC),
            )
            # Без явного commit профиль, версия и аудит должны откатиться.
        with pytest.raises(IntegrityError):
            async with SqlAlchemyUnitOfWork(factory) as work:
                await work.users.replace_review_access(
                    updated,
                    expected_authorization_version=2,
                    actor_user_id=uuid4(),
                    created_at=datetime.now(UTC),
                )
                await work.commit()
        async with SqlAlchemyUnitOfWork(factory) as work:
            saved = await work.users.get_user(target_id)
        assert saved.authorization_version == 2
        assert saved.review_access_enabled is False
        async with test_engine.connect() as connection:
            assert (
                await connection.execute(
                    select(ReviewAccessEventModel.event_id).where(
                        ReviewAccessEventModel.user_id == target_id
                    )
                )
            ).all() == []
    finally:
        await remove_test_users(test_engine, target_id)


@pytest.mark.asyncio
async def test_user_migration_columns_match_orm_contract(test_engine):
    """Каждая ORM-колонка должна существовать после миграций собственной схемы."""
    async with test_engine.connect() as connection:

        def verify_columns(sync_connection):
            """Сравнивает БД только с метаданными users, без чужих сервисов."""
            inspector = inspect(sync_connection)
            for table in Base.metadata.sorted_tables:
                assert table.schema == "users"
                actual = {
                    column["name"]
                    for column in inspector.get_columns(table.name, schema="users")
                }
                assert actual == set(table.columns.keys()), table.name

        await connection.run_sync(verify_columns)
