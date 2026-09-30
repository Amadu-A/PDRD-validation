# services/user-service/tests/unit/test_access.py

"""Поведенческие тесты матрицы прав и запрета привилегий гостя."""

import pytest
from pdrd_user_service.domain.access import (
    AccessSubject,
    AccessTier,
    Permission,
    Role,
    effective_permissions,
    has_operation_permission,
)


def test_guest_can_analyze_but_cannot_change_protected_data() -> None:
    """Гость выполняет временный анализ без доступа к U, Review и настройкам."""
    guest = AccessSubject(AccessTier.GUEST)

    assert effective_permissions(guest) == {
        Permission.ANALYSIS_INPUT_UPLOAD,
        Permission.ANALYSIS_RUN,
        Permission.ANALYSIS_RESULT_READ,
        Permission.ANALYSIS_RESULT_DOWNLOAD,
        Permission.NORMATIVE_CATALOG_READ,
    }
    for permission in (
        Permission.USER_DOCUMENT_OWN_WRITE,
        Permission.NORMATIVE_WRITE,
        Permission.NORMATIVE_DELETE,
        Permission.SYSTEM_PROMPT_MANAGE,
        Permission.REVIEW_GOLD_CREATE,
        Permission.REVIEW_FINDINGS_DECIDE,
        Permission.REVIEW_APPROVE,
        Permission.EXPERIENCE_CAPTURE,
        Permission.ADMIN_ACCESS,
    ):
        assert not has_operation_permission(guest, permission)


def test_verified_free_account_adds_profile_without_review_rights() -> None:
    """Бесплатная регистрация сохраняет гостевой анализ и личный кабинет."""
    free = AccessSubject(AccessTier.REGISTERED_FREE)

    assert effective_permissions(free) - effective_permissions(
        AccessSubject(AccessTier.GUEST)
    ) == {Permission.PROFILE_READ, Permission.PROFILE_UPDATE}
    assert has_operation_permission(free, Permission.NORMATIVE_CATALOG_READ)
    assert not has_operation_permission(free, Permission.NORMATIVE_WRITE)
    assert not has_operation_permission(free, Permission.REVIEW_OWN_READ)
    assert not has_operation_permission(free, Permission.USER_DOCUMENT_OWN_READ)


def test_designer_can_create_gold_but_cannot_approve_or_capture() -> None:
    """Проектировщик готовит Gold без решения за руководителя."""
    designer = AccessSubject(AccessTier.MEMBER, frozenset({Role.DESIGNER}))

    assert has_operation_permission(designer, Permission.REVIEW_GOLD_CREATE)
    assert has_operation_permission(designer, Permission.USER_DOCUMENT_OWN_WRITE)
    assert not has_operation_permission(designer, Permission.USER_DOCUMENT_SCOPED_WRITE)
    assert not has_operation_permission(designer, Permission.REVIEW_FINDINGS_DECIDE)
    assert not has_operation_permission(designer, Permission.REVIEW_APPROVE)
    assert not has_operation_permission(designer, Permission.EXPERIENCE_CAPTURE)


def test_head_can_approve_and_capture_without_prompt_management() -> None:
    """Руководитель утверждает Review и вручную публикует проверенный опыт."""
    head = AccessSubject(AccessTier.MEMBER, frozenset({Role.DEPARTMENT_HEAD}))

    assert has_operation_permission(head, Permission.REVIEW_SCOPED_READ)
    assert has_operation_permission(head, Permission.REVIEW_FINDINGS_DECIDE)
    assert has_operation_permission(head, Permission.REVIEW_APPROVE)
    assert has_operation_permission(head, Permission.EXPERIENCE_CAPTURE)
    assert not has_operation_permission(head, Permission.SYSTEM_PROMPT_MANAGE)
    assert not has_operation_permission(head, Permission.ADMIN_ACCESS)


def test_changed_role_changes_effective_operations() -> None:
    """Пересчёт операций использует текущую доверенную роль."""
    designer = AccessSubject(AccessTier.MEMBER, frozenset({Role.DESIGNER}))
    head = AccessSubject(AccessTier.MEMBER, frozenset({Role.DEPARTMENT_HEAD}))

    assert not has_operation_permission(designer, Permission.EXPERIENCE_CAPTURE)
    assert has_operation_permission(head, Permission.EXPERIENCE_CAPTURE)


def test_admin_gets_only_enumerated_operations() -> None:
    """Администратор получает реестр операций без права произвольного SQL."""
    admin = AccessSubject(AccessTier.MEMBER, frozenset({Role.PLATFORM_ADMIN}))

    assert effective_permissions(admin) == frozenset(Permission)
    assert not any("sql" in permission.value for permission in Permission)
    with pytest.raises(TypeError):
        has_operation_permission(admin, "database.execute_sql")  # type: ignore[arg-type]


@pytest.mark.parametrize("tier", [AccessTier.REGISTERED_FREE, AccessTier.MEMBER])
def test_inactive_account_has_no_permissions(tier: AccessTier) -> None:
    """Политика не выдаёт операций неактивному аккаунту."""
    roles = (
        frozenset({Role.PLATFORM_ADMIN}) if tier is AccessTier.MEMBER else frozenset()
    )
    blocked = AccessSubject(tier, roles, active=False)

    assert effective_permissions(blocked) == frozenset()


def test_free_identity_cannot_claim_role() -> None:
    """Роль нельзя подставить в гостевой или бесплатный контекст."""
    for tier in (AccessTier.GUEST, AccessTier.REGISTERED_FREE):
        with pytest.raises(ValueError, match="Роли доступны"):
            AccessSubject(tier, frozenset({Role.PLATFORM_ADMIN}))


def test_untrusted_role_string_and_mutable_set_are_rejected() -> None:
    """Сырые строки и изменяемые роли не становятся серверными назначениями."""
    with pytest.raises(TypeError, match="только Role"):
        AccessSubject(AccessTier.MEMBER, frozenset({"platform_admin"}))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="frozenset"):
        AccessSubject(AccessTier.MEMBER, {Role.PLATFORM_ADMIN})  # type: ignore[arg-type]


def test_member_without_role_has_only_free_operations() -> None:
    """Успешный вход без назначенной роли не открывает внутренние данные."""
    member = AccessSubject(AccessTier.MEMBER)
    free = AccessSubject(AccessTier.REGISTERED_FREE)

    assert effective_permissions(member) == effective_permissions(free)
    assert not has_operation_permission(member, Permission.NORMATIVE_WRITE)


def test_permission_result_cannot_be_mutated() -> None:
    """Получатель списка возможностей не меняет матрицу доступа."""
    permissions = effective_permissions(AccessSubject(AccessTier.GUEST))

    assert isinstance(permissions, frozenset)
    with pytest.raises(AttributeError):
        permissions.add(Permission.ADMIN_ACCESS)  # type: ignore[attr-defined]
