# services/user-service/tests/unit/test_section_distribution.py

"""Проверяет одноразовую выдачу каталога без изменения ролей и версий сессий."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pdrd_user_service.application.use_cases.distribute_section import (
    CatalogSectionNotFound,
    CatalogWriteRequired,
    DistributeSection,
)
from pdrd_user_service.domain.access import AccessTier, Role
from pdrd_user_service.domain.identity import UserAccount, UserKind, UserStatus
from pdrd_user_service.domain.role_assignments import (
    RoleAssignment,
    RoleScope,
    RoleSource,
    ScopeKind,
)

AT = datetime(2026, 10, 6, tzinfo=UTC)
NEW = UUID("eeeeeeee-eeee-4eee-eeee-eeeeeeeeeeee")
OLD = UUID("ffffffff-ffff-4fff-ffff-ffffffffffff")
HEAD = UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")
DESIGNER = UUID("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb")
BLOCKED = UUID("cccccccc-cccc-4ccc-cccc-cccccccccccc")
EMPTY = UUID("dddddddd-dddd-4ddd-dddd-dddddddddddd")
EXPIRED = UUID("99999999-9999-4999-8999-999999999999")


def profile(user_id: UUID) -> UserAccount:
    """Создаёт активного сотрудника с неизменяемой версией прав."""
    return UserAccount(
        user_id=user_id,
        kind=UserKind.CORPORATE,
        tier=AccessTier.MEMBER,
        status=UserStatus.ACTIVE,
        display_name="Сотрудник",
        login=str(user_id),
        created_at=AT - timedelta(days=1),
        authorization_version=7,
    )


def assignment(user_id: UUID, role: Role) -> RoleAssignment:
    """Создаёт актуальное серверное назначение с собственной областью роли."""
    scope = ScopeKind.SECTIONS if role is Role.DEPARTMENT_HEAD else ScopeKind.OWN
    return RoleAssignment(
        assignment_id=uuid4(),
        user_id=user_id,
        role=role,
        source=RoleSource.LOCAL,
        scope=RoleScope(scope),
        created_at=AT - timedelta(days=1),
    )


class Users:
    """Хранит данные и markers для независимой проверки прикладной политики."""

    def __init__(self) -> None:
        """Создаёт активные, заблокированные, неназначенные и истёкшие профили."""
        self.accounts = {
            user_id: profile(user_id)
            for user_id in (HEAD, DESIGNER, BLOCKED, EMPTY, EXPIRED)
        }
        self.accounts[BLOCKED] = replace(
            self.accounts[BLOCKED], status=UserStatus.BLOCKED
        )
        self.assignments = {
            HEAD: (assignment(HEAD, Role.DEPARTMENT_HEAD),),
            DESIGNER: (assignment(DESIGNER, Role.DESIGNER),),
            BLOCKED: (assignment(BLOCKED, Role.DESIGNER),),
            EXPIRED: (
                replace(
                    assignment(EXPIRED, Role.DESIGNER),
                    expires_at=AT - timedelta(hours=1),
                ),
            ),
        }
        self.sections = dict.fromkeys(self.accounts, (OLD,))
        self.markers: set[UUID] = set()
        self.audit: list[tuple[UUID, int, tuple[UUID, ...]]] = []
        self.fail_user: UUID | None = None

    async def get_user(self, user_id: UUID) -> UserAccount | None:
        """Читает профиль действующего актёра."""
        return self.accounts.get(user_id)

    async def list_assignments(self, user_id: UUID) -> tuple[RoleAssignment, ...]:
        """Не принимает роль из браузерных данных."""
        return self.assignments.get(user_id, ())

    async def list_memberships(self, user_id: UUID) -> tuple[object, ...]:
        """Области sections и own не требуют прежнего отдела."""
        del user_id
        return ()

    async def lock_section_catalog(self) -> None:
        """Имитирует транзакционный lock для последовательных команд."""

    async def lock_catalog_users(self, actor_user_id: UUID) -> tuple[UserAccount, ...]:
        """Возвращает профили в том же порядке, что PostgreSQL rowlocks."""
        return tuple(
            self.accounts[user_id]
            for user_id in sorted(self.accounts)
            if user_id == actor_user_id
            or self.accounts[user_id].status is UserStatus.ACTIVE
        )

    async def reserve_section_distribution(
        self, section_id: UUID, actor_user_id: UUID, created_at: datetime
    ) -> bool:
        """Не резервирует повторное восстановление ручного отзыва доступа."""
        del actor_user_id, created_at
        if section_id in self.markers:
            return False
        self.markers.add(section_id)
        return True

    async def list_sections(self, user_id: UUID) -> tuple[UUID, ...]:
        """Читает прежде назначенные разделы, включая ручные ограничения."""
        return self.sections.get(user_id, ())

    async def replace_sections(
        self,
        user_id: UUID,
        section_ids: tuple[UUID, ...],
        *,
        actor_user_id: UUID,
        authorization_version: int,
        created_at: datetime,
    ) -> None:
        """Сохраняет новый набор и аудит, сохраняя версию пользователя."""
        del actor_user_id, created_at
        if user_id == self.fail_user:
            raise RuntimeError("Сбой сохранения второго пользователя")
        assert authorization_version == self.accounts[user_id].authorization_version
        self.sections[user_id] = section_ids
        self.audit.append((user_id, authorization_version, section_ids))


class Work:
    """Откатывает marker и записи, если хотя бы одна выдача не сохранена."""

    def __init__(self, users: Users) -> None:
        """Хранит единый репозиторий всех команд теста."""
        self.users = users
        self.committed = False

    async def __aenter__(self) -> "Work":
        """Создаёт изолированный снимок перед транзакцией."""
        self.previous = deepcopy(self.users.__dict__)
        return self

    async def __aexit__(self, *args: object) -> None:
        """Восстанавливает данные при отсутствии commit."""
        del args
        if not self.committed:
            self.users.__dict__.clear()
            self.users.__dict__.update(self.previous)

    async def commit(self) -> None:
        """Разрешает сохранить все изменения вместе."""
        self.committed = True


class Catalog:
    """Предоставляет актуальный исходный UUID без обращения к чужой БД."""

    async def list_section_ids(self) -> tuple[UUID, ...]:
        """Возвращает новый раздел после подтверждённого создания."""
        return (NEW,)


@pytest.mark.asyncio
async def test_distribution_adds_only_to_active_designer_and_head_preserving_versions() -> (
    None
):
    """Рабочие роли получают новый UUID, блокировки и истёкшие роли сохраняются."""
    users = Users()
    before = dict(users.accounts)
    service = DistributeSection(lambda: Work(users), Catalog(), clock=lambda: AT)
    result = await service.execute(section_id=NEW, actor_user_id=HEAD)
    assert result.granted_users == 2
    assert result.already_distributed is False
    assert users.accounts == before
    assert set(users.sections[HEAD]) == set(users.sections[DESIGNER]) == {OLD, NEW}
    assert (
        users.sections[BLOCKED]
        == users.sections[EMPTY]
        == users.sections[EXPIRED]
        == (OLD,)
    )
    assert [version for _, version, _ in users.audit] == [7, 7]


@pytest.mark.asyncio
async def test_retry_does_not_restore_admin_revocation_or_repeat_audit() -> None:
    """Marker после успешной выдачи защищает снятые администратором чекбоксы."""
    users = Users()
    service = DistributeSection(lambda: Work(users), Catalog(), clock=lambda: AT)
    await service.execute(section_id=NEW, actor_user_id=HEAD)
    users.sections[DESIGNER] = ()
    repeated = await service.execute(section_id=NEW, actor_user_id=HEAD)
    assert repeated.already_distributed is True
    assert repeated.granted_users == 0
    assert users.sections[DESIGNER] == ()
    assert len(users.audit) == 2


@pytest.mark.asyncio
async def test_failure_rolls_back_marker_and_previous_grants_so_retry_completes() -> (
    None
):
    """Ошибка посреди выдачи не оставляет частичный доступ или погашенный marker."""
    users = Users()
    users.fail_user = DESIGNER
    service = DistributeSection(lambda: Work(users), Catalog(), clock=lambda: AT)
    with pytest.raises(RuntimeError, match="второго пользователя"):
        await service.execute(section_id=NEW, actor_user_id=HEAD)
    assert users.markers == set()
    assert users.sections[HEAD] == (OLD,)
    assert users.audit == []
    users.fail_user = None
    assert (
        await service.execute(section_id=NEW, actor_user_id=HEAD)
    ).granted_users == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", [DESIGNER, BLOCKED, EXPIRED])
async def test_distribution_requires_live_writer_role(actor: UUID) -> None:
    """Проектировщик, заблокированный и истёкший субъект не меняют доступ."""
    users = Users()
    service = DistributeSection(lambda: Work(users), Catalog(), clock=lambda: AT)
    with pytest.raises(CatalogWriteRequired):
        await service.execute(section_id=NEW, actor_user_id=actor)
    assert users.markers == set()
    assert users.audit == []


@pytest.mark.asyncio
async def test_distribution_requires_existing_knowledge_section() -> None:
    """UUID, отсутствующий в исходном каталоге, не выдаётся рабочим ролям."""
    users = Users()
    service = DistributeSection(lambda: Work(users), Catalog(), clock=lambda: AT)
    with pytest.raises(CatalogSectionNotFound):
        await service.execute(section_id=OLD, actor_user_id=HEAD)
    assert users.markers == set()
