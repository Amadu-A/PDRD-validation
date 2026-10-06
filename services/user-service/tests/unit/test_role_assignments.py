# services/user-service/tests/unit/test_role_assignments.py

"""Проверяет сроки, источники и границы назначения ролей пользователей PDRD."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pdrd_user_service.domain.access import (
    AccessTier,
    Permission,
    Role,
    has_operation_permission,
)
from pdrd_user_service.domain.identity import (
    Membership,
    UserAccount,
    UserKind,
    UserStatus,
)
from pdrd_user_service.domain.role_assignments import (
    ResourceScope,
    RoleAssignment,
    RoleScope,
    RoleSource,
    ScopeKind,
    access_subject_for,
    assign_role,
    effective_roles,
    has_role_for_resource,
    revoke_role,
)

USER_ID = UUID("e287440a-a99a-4d29-8d1a-fafcd9388209")
OTHER_USER_ID = UUID("f681124a-76dc-4719-ab8b-84f3f42ac6f9")
ORGANIZATION_ID = UUID("c13ec5d5-b7ab-4fdc-a475-a5a49932cbfa")
OTHER_ORGANIZATION_ID = UUID("877dff8c-dc55-4fe0-9928-cddbf09a78ef")
DEPARTMENT_ID = UUID("9dfbf101-c19d-4521-8674-426b0287a0e5")
OTHER_DEPARTMENT_ID = UUID("43f3b1d6-a87d-4df2-a73c-67b9a9f45df4")
ASSIGNMENT_ID = UUID("66504634-fd64-4748-a997-f3736df5b6a2")
OTHER_ASSIGNMENT_ID = UUID("16970080-e522-413b-b27e-de5cdb527160")
AT = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def corporate_user() -> UserAccount:
    """Создаёт активного участника без сохранения пароля Active Directory."""
    return UserAccount(
        user_id=USER_ID,
        kind=UserKind.CORPORATE,
        tier=AccessTier.MEMBER,
        status=UserStatus.ACTIVE,
        display_name="Иван Мейн",
        created_at=AT - timedelta(days=1),
        login="i.mein",
    )


def designer_assignment(**changes: object) -> RoleAssignment:
    """Создаёт локальную роль проектировщика для повторяемых проверок."""
    assignment = RoleAssignment(
        assignment_id=ASSIGNMENT_ID,
        user_id=USER_ID,
        role=Role.DESIGNER,
        source=RoleSource.LOCAL,
        scope=RoleScope(ScopeKind.OWN),
        created_at=AT - timedelta(hours=1),
    )
    return replace(assignment, **changes)


def head_assignment(**changes: object) -> RoleAssignment:
    """Создаёт назначение руководителя строго для одного отдела."""
    assignment = RoleAssignment(
        assignment_id=ASSIGNMENT_ID,
        user_id=USER_ID,
        role=Role.DEPARTMENT_HEAD,
        source=RoleSource.LOCAL,
        scope=RoleScope(ScopeKind.DEPARTMENT, ORGANIZATION_ID, DEPARTMENT_ID),
        created_at=AT - timedelta(hours=1),
    )
    return replace(assignment, **changes)


def platform_admin_assignment() -> RoleAssignment:
    """Создаёт администраторское назначение для проверки защищённого пути."""
    return RoleAssignment(
        assignment_id=ASSIGNMENT_ID,
        user_id=USER_ID,
        role=Role.PLATFORM_ADMIN,
        source=RoleSource.LOCAL,
        scope=RoleScope(ScopeKind.PLATFORM),
        created_at=AT - timedelta(hours=1),
    )


def department_membership(**changes: object) -> Membership:
    """Создаёт действующее членство пользователя в отделе."""
    return replace(
        Membership(USER_ID, ORGANIZATION_ID, DEPARTMENT_ID),
        **changes,
    )


def test_local_designer_assignment_changes_permissions_and_version() -> None:
    """Роль становится доступной после назначения и обновляет версию прав."""
    user = corporate_user()
    assignment = designer_assignment()

    updated_user, assignments = assign_role(user, assignment, (), (), AT)

    assert user.authorization_version == 1
    assert updated_user.authorization_version == 2
    assert assignments == (assignment,)
    assert effective_roles(updated_user, assignments, (), AT) == {Role.DESIGNER}
    subject = access_subject_for(updated_user, assignments, (), AT)
    assert not has_operation_permission(subject, Permission.REVIEW_GOLD_CREATE)
    assert not has_operation_permission(subject, Permission.REVIEW_APPROVE)


def test_role_is_active_only_inside_its_time_window() -> None:
    """Назначение не действует до создания и после срока окончания."""
    assignment = designer_assignment(expires_at=AT + timedelta(hours=1))

    assert not assignment.is_active_at(assignment.created_at - timedelta(seconds=1))
    assert assignment.is_active_at(assignment.created_at)
    assert assignment.is_active_at(AT)
    assert not assignment.is_active_at(assignment.expires_at)


def test_revoked_role_remains_visible_at_historical_time() -> None:
    """История прав до отзыва сохраняется, а в момент отзыва право исчезает."""
    revoked = designer_assignment(revoked_at=AT)

    assert revoked.is_active_at(AT - timedelta(seconds=1))
    assert not revoked.is_active_at(AT)
    assert effective_roles(
        corporate_user(), (revoked,), (), AT - timedelta(seconds=1)
    ) == {Role.DESIGNER}
    assert effective_roles(corporate_user(), (revoked,), (), AT) == frozenset()


def test_revocation_increments_version_and_removes_current_permission() -> None:
    """Отзыв атомарно меняет назначение и версию полномочий пользователя."""
    user = corporate_user()
    updated_user, assignments = revoke_role(
        user, ASSIGNMENT_ID, (designer_assignment(),), AT
    )

    assert user.authorization_version == 1
    assert updated_user.authorization_version == 2
    assert assignments[0].revoked_at == AT
    subject = access_subject_for(updated_user, assignments, (), AT)
    assert not has_operation_permission(subject, Permission.REVIEW_GOLD_CREATE)


def test_cannot_revoke_another_users_assignment() -> None:
    """Совпавший идентификатор не позволяет отозвать право другого человека."""
    user = corporate_user()
    foreign = designer_assignment(user_id=OTHER_USER_ID)

    with pytest.raises(ValueError):
        revoke_role(user, foreign.assignment_id, (foreign,), AT)

    assert user.authorization_version == 1
    assert foreign.revoked_at is None


@pytest.mark.parametrize(
    "membership",
    [
        department_membership(user_id=OTHER_USER_ID),
        department_membership(organization_id=OTHER_ORGANIZATION_ID),
        department_membership(department_id=OTHER_DEPARTMENT_ID),
        department_membership(active=False),
    ],
)
def test_head_role_needs_active_membership_in_exact_department(
    membership: Membership,
) -> None:
    """Назначение руководителя отвергается вне его организации и отдела."""
    with pytest.raises(ValueError):
        assign_role(corporate_user(), head_assignment(), (), (membership,), AT)

    assert (
        effective_roles(corporate_user(), (head_assignment(),), (membership,), AT)
        == frozenset()
    )


def test_head_role_is_effective_with_matching_department_membership() -> None:
    """Точное активное членство позволяет применять роль руководителя."""
    user = corporate_user()
    membership = department_membership()

    updated_user, assignments = assign_role(
        user, head_assignment(), (), (membership,), AT
    )

    assert updated_user.authorization_version == 2
    assert effective_roles(updated_user, assignments, (membership,), AT) == {
        Role.DEPARTMENT_HEAD
    }


def test_own_scope_never_grants_access_to_another_users_document() -> None:
    """Личная роль проектировщика действует только на его объект."""
    user = corporate_user()
    assignment = designer_assignment()
    own_resource = ResourceScope(USER_ID, ORGANIZATION_ID, DEPARTMENT_ID)
    foreign_resource = ResourceScope(OTHER_USER_ID, ORGANIZATION_ID, DEPARTMENT_ID)
    memberships = (department_membership(),)

    assert has_role_for_resource(
        user, (assignment,), memberships, Role.DESIGNER, own_resource, AT
    )
    assert not has_role_for_resource(
        user, (assignment,), memberships, Role.DESIGNER, foreign_resource, AT
    )
    assert not has_role_for_resource(
        user, (assignment,), memberships, Role.DEPARTMENT_HEAD, own_resource, AT
    )


def test_own_scope_still_requires_membership_for_organization_resource() -> None:
    """Владелец не читает старый ресурс после выхода из организации или отдела."""
    user = corporate_user()
    assignment = designer_assignment()
    own_resource = ResourceScope(USER_ID, ORGANIZATION_ID, DEPARTMENT_ID)
    foreign_organization = ResourceScope(USER_ID, OTHER_ORGANIZATION_ID, DEPARTMENT_ID)
    foreign_department = ResourceScope(USER_ID, ORGANIZATION_ID, OTHER_DEPARTMENT_ID)
    membership = department_membership()

    for resource in (foreign_organization, foreign_department):
        assert not has_role_for_resource(
            user, (assignment,), (membership,), Role.DESIGNER, resource, AT
        )
    assert not has_role_for_resource(
        user, (assignment,), (), Role.DESIGNER, own_resource, AT
    )
    assert not has_role_for_resource(
        user,
        (assignment,),
        (replace(membership, active=False),),
        Role.DESIGNER,
        own_resource,
        AT,
    )
    assert has_role_for_resource(
        user, (assignment,), (), Role.DESIGNER, ResourceScope(USER_ID), AT
    )


def test_department_scope_never_crosses_organization_or_department() -> None:
    """Роль руководителя не выходит за границы назначенного отдела."""
    user = corporate_user()
    assignment = head_assignment()
    memberships = (department_membership(),)
    own_department = ResourceScope(OTHER_USER_ID, ORGANIZATION_ID, DEPARTMENT_ID)
    foreign_organization = ResourceScope(
        OTHER_USER_ID, OTHER_ORGANIZATION_ID, DEPARTMENT_ID
    )
    foreign_department = ResourceScope(
        OTHER_USER_ID, ORGANIZATION_ID, OTHER_DEPARTMENT_ID
    )

    assert has_role_for_resource(
        user, (assignment,), memberships, Role.DEPARTMENT_HEAD, own_department, AT
    )
    for resource in (foreign_organization, foreign_department):
        assert not has_role_for_resource(
            user, (assignment,), memberships, Role.DEPARTMENT_HEAD, resource, AT
        )
    assert not has_role_for_resource(
        user, (assignment,), memberships, Role.PLATFORM_ADMIN, own_department, AT
    )


def test_expired_or_revoked_assignment_does_not_block_new_local_assignment() -> None:
    """Историческое назначение не мешает восстановить роль с новым ID."""
    for previous in (
        designer_assignment(expires_at=AT),
        designer_assignment(revoked_at=AT),
    ):
        replacement = designer_assignment(assignment_id=OTHER_ASSIGNMENT_ID)

        updated_user, assignments = assign_role(
            corporate_user(), replacement, (previous,), (), AT
        )

        assert updated_user.authorization_version == 2
        assert assignments == (previous, replacement)


def test_duplicate_assignment_id_or_active_role_is_rejected() -> None:
    """Повторный ID и параллельная одинаковая роль не создают двойных прав."""
    original = designer_assignment()
    candidates = (
        original,
        designer_assignment(assignment_id=OTHER_ASSIGNMENT_ID),
    )

    for candidate in candidates:
        with pytest.raises(ValueError):
            assign_role(corporate_user(), candidate, (original,), (), AT)


def test_assignment_to_another_user_is_rejected() -> None:
    """Поле user_id назначения не может подменить запрошенный профиль."""
    with pytest.raises(ValueError):
        assign_role(
            corporate_user(),
            designer_assignment(user_id=OTHER_USER_ID),
            (),
            (),
            AT,
        )


@pytest.mark.parametrize(
    "user",
    [
        replace(corporate_user(), status=UserStatus.BLOCKED),
        UserAccount(
            USER_ID,
            UserKind.EXTERNAL,
            AccessTier.REGISTERED_FREE,
            UserStatus.ACTIVE,
            "Внешний пользователь",
            AT - timedelta(days=1),
            email="external@example.org",
        ),
        UserAccount(
            USER_ID,
            UserKind.EXTERNAL,
            AccessTier.REGISTERED_FREE,
            UserStatus.PENDING_VERIFICATION,
            "Внешний пользователь",
            AT - timedelta(days=1),
            email="external@example.org",
        ),
    ],
)
def test_blocked_pending_and_free_users_cannot_receive_roles(user: UserAccount) -> None:
    """Статус или бесплатный тариф не повышается простым назначением роли."""
    with pytest.raises(ValueError):
        assign_role(user, designer_assignment(), (), (), AT)

    assert effective_roles(user, (designer_assignment(),), (), AT) == frozenset()
    subject = access_subject_for(user, (designer_assignment(),), (), AT)
    assert not has_operation_permission(subject, Permission.REVIEW_GOLD_CREATE)


def test_platform_admin_uses_separate_protected_assignment_path() -> None:
    """Обычная операция назначения не выдаёт платформенного администратора."""
    with pytest.raises(ValueError):
        assign_role(corporate_user(), platform_admin_assignment(), (), (), AT)


def test_platform_admin_cannot_be_revoked_through_generic_role_path() -> None:
    """Отзыв администратора требует отдельного защищённого процесса."""
    user = corporate_user()
    assignment = platform_admin_assignment()

    with pytest.raises(ValueError):
        revoke_role(user, assignment.assignment_id, (assignment,), AT)

    assert user.authorization_version == 1
    assert assignment.revoked_at is None


def test_unverified_ad_group_cannot_grant_or_assign_role() -> None:
    """Модель будущей AD-привязки не включает права без проверенного mapping."""
    assignment = head_assignment(
        source=RoleSource.AD_GROUP,
        external_group_id="ad-group-object-guid",
    )
    membership = department_membership()

    with pytest.raises(ValueError):
        assign_role(corporate_user(), assignment, (), (membership,), AT)

    assert effective_roles(corporate_user(), (assignment,), (membership,), AT) == (
        frozenset()
    )
    resource = ResourceScope(OTHER_USER_ID, ORGANIZATION_ID, DEPARTMENT_ID)
    assert not has_role_for_resource(
        corporate_user(),
        (assignment,),
        (membership,),
        Role.DEPARTMENT_HEAD,
        resource,
        AT,
    )


def test_assignment_time_and_scope_require_valid_values() -> None:
    """Неверные сроки и области не сохраняются как привилегии."""
    with pytest.raises(ValueError):
        designer_assignment(expires_at=AT - timedelta(hours=2))
    with pytest.raises(ValueError):
        RoleScope(ScopeKind.DEPARTMENT, ORGANIZATION_ID)
    with pytest.raises(ValueError):
        RoleScope(ScopeKind.OWN, ORGANIZATION_ID)
    with pytest.raises(ValueError):
        designer_assignment(
            scope=RoleScope(ScopeKind.DEPARTMENT, ORGANIZATION_ID, DEPARTMENT_ID)
        )


def test_naive_datetime_cannot_drive_role_decision() -> None:
    """Сравнение прав требует даты с часовым поясом."""
    naive = datetime(2026, 9, 30, 12, 0)

    with pytest.raises(ValueError):
        designer_assignment().is_active_at(naive)
    with pytest.raises(ValueError):
        effective_roles(corporate_user(), (designer_assignment(),), (), naive)
