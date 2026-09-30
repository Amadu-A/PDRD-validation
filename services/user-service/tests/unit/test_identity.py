# services/user-service/tests/unit/test_identity.py

"""Проверяет профили и устойчивое сопоставление пользователей без AD runtime."""

from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest
from pdrd_user_service.domain.access import AccessTier
from pdrd_user_service.domain.identity import (
    Department,
    ExternalIdentity,
    Membership,
    Organization,
    UserAccount,
    UserKind,
    UserStatus,
)

USER_ID = UUID("3c1ad891-a6a5-46eb-85b2-421c32bfae5b")
OTHER_USER_ID = UUID("9eac18b0-f7b7-4ab4-842b-ab1393fa6597")
ORGANIZATION_ID = UUID("4e16d7cd-8af8-4f05-b6c5-f375819ac3a1")
OTHER_ORGANIZATION_ID = UUID("0d77b570-bf5e-4c2b-92ae-114f0317ca88")
DEPARTMENT_ID = UUID("eea609bd-fe93-418d-ae53-b6be28dd6648")
CREATED_AT = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)


def corporate_user() -> UserAccount:
    """Возвращает корпоративный профиль с локальным UUID PDRD."""
    return UserAccount(
        user_id=USER_ID,
        kind=UserKind.CORPORATE,
        tier=AccessTier.MEMBER,
        status=UserStatus.ACTIVE,
        display_name="Иван Мейн",
        created_at=CREATED_AT,
        login="i.mein",
        email="ivan@example.org",
    )


def test_identity_key_is_independent_of_mutable_profile_fields() -> None:
    """Изменение логина или email не переносит вход к другому аккаунту."""
    identity = ExternalIdentity(
        "active_directory", "corp.example", "object-guid", USER_ID
    )
    renamed = replace(corporate_user(), login="i.new", email="new@example.org")

    assert identity.stable_key == ("active_directory", "corp.example", "object-guid")
    assert identity.user_id == renamed.user_id
    assert identity.stable_key != (
        "active_directory",
        "corp.example",
        renamed.email,
    )


def test_provider_namespace_and_subject_all_form_the_identity_key() -> None:
    """Совпадение subject у разных провайдеров или доменов не склеивает людей."""
    original = ExternalIdentity("ad", "domain-a", "same-subject", USER_ID)
    other_domain = ExternalIdentity("ad", "domain-b", "same-subject", OTHER_USER_ID)
    local_account = ExternalIdentity("local", "pdrd", "same-subject", OTHER_USER_ID)

    assert (
        len({item.stable_key for item in (original, other_domain, local_account)}) == 3
    )


@pytest.mark.parametrize("field_name", ["provider_id", "namespace", "subject"])
def test_external_identity_rejects_missing_stable_key_part(field_name: str) -> None:
    """Неполный ключ не может стать учётной привязкой."""
    values = {
        "provider_id": "ad",
        "namespace": "domain-a",
        "subject": "stable-id",
        "user_id": USER_ID,
    }
    values[field_name] = "  "

    with pytest.raises(ValueError, match=field_name):
        ExternalIdentity(**values)


def test_persisted_user_cannot_be_guest() -> None:
    """Гостевой анализ не создаёт запись пользователя."""
    with pytest.raises(ValueError, match="Гость"):
        replace(corporate_user(), tier=AccessTier.GUEST)


def test_corporate_user_does_not_use_local_email_verification_state() -> None:
    """Корпоративный профиль не проходит регистрацию по email."""
    with pytest.raises(ValueError, match="Корпоративный"):
        replace(corporate_user(), status=UserStatus.PENDING_VERIFICATION)


def test_active_external_account_needs_email() -> None:
    """Подтверждённый внешний профиль имеет адрес для личного кабинета."""
    with pytest.raises(ValueError, match="email"):
        UserAccount(
            USER_ID,
            UserKind.EXTERNAL,
            AccessTier.REGISTERED_FREE,
            UserStatus.ACTIVE,
            "Внешний пользователь",
            CREATED_AT,
        )


def test_pending_external_profile_is_representable_without_role() -> None:
    """Состояние ожидания регистрации можно хранить без назначения роли."""
    pending = UserAccount(
        USER_ID,
        UserKind.EXTERNAL,
        AccessTier.REGISTERED_FREE,
        UserStatus.PENDING_VERIFICATION,
        "Внешний пользователь",
        CREATED_AT,
        email="new@example.org",
    )

    assert pending.status is UserStatus.PENDING_VERIFICATION
    assert pending.tier is AccessTier.REGISTERED_FREE


def test_active_corporate_account_requires_ad_login() -> None:
    """Профиль сотрудника без корпоративного логина нельзя активировать."""
    with pytest.raises(ValueError, match="login"):
        replace(corporate_user(), login=None)

    blocked = replace(corporate_user(), status=UserStatus.BLOCKED, login=None)
    assert blocked.status is UserStatus.BLOCKED


def test_pending_external_account_requires_email_and_has_no_login_history() -> None:
    """Регистрация по email предшествует первому входу пользователя."""
    pending = UserAccount(
        USER_ID,
        UserKind.EXTERNAL,
        AccessTier.REGISTERED_FREE,
        UserStatus.PENDING_VERIFICATION,
        "Внешний пользователь",
        CREATED_AT,
        email="new@example.org",
    )

    with pytest.raises(ValueError, match="email"):
        replace(pending, email=None)
    with pytest.raises(ValueError, match="last_login_at"):
        replace(pending, last_login_at=CREATED_AT + timedelta(minutes=1))


def test_last_login_time_is_absolute_and_not_before_creation() -> None:
    """Время последнего входа сравнивается с созданием с учётом пояса."""
    user = corporate_user()
    same_instant = CREATED_AT.astimezone(timezone(timedelta(hours=3)))
    assert replace(user, last_login_at=same_instant).last_login_at == CREATED_AT

    with pytest.raises(ValueError, match="last_login_at"):
        replace(user, last_login_at=CREATED_AT - timedelta(microseconds=1))


@pytest.mark.parametrize("field_name", ["created_at", "last_login_at"])
def test_account_times_must_have_timezone(field_name: str) -> None:
    """Наивное местное время не используется для аудита входов."""
    with pytest.raises(ValueError, match=field_name):
        replace(corporate_user(), **{field_name: CREATED_AT.replace(tzinfo=None)})


@pytest.mark.parametrize("field_name", ["created_at", "last_login_at"])
def test_account_times_must_be_datetime(field_name: str) -> None:
    """HTTP-строка не подменяет типизированное событие профиля."""
    with pytest.raises(TypeError, match=field_name):
        replace(corporate_user(), **{field_name: "2026-09-30T09:00:00Z"})


@pytest.mark.parametrize("version", [0, -1])
def test_authorization_version_must_be_positive(version: int) -> None:
    """Версия прав пригодна для инвалидизации устаревшего доступа."""
    with pytest.raises(ValueError, match="authorization_version"):
        replace(corporate_user(), authorization_version=version)


def test_authorization_version_cannot_be_boolean() -> None:
    """Булево значение не становится версией прав через Python int."""
    with pytest.raises(TypeError, match="authorization_version"):
        replace(corporate_user(), authorization_version=True)


def test_identity_models_do_not_store_credentials() -> None:
    """Поля user-service не содержат пароль AD или хеш локального пароля."""
    names = {
        field.name
        for model in (UserAccount, ExternalIdentity)
        for field in fields(model)
    }

    assert not any("password" in name or "secret" in name for name in names)
    with pytest.raises(FrozenInstanceError):
        corporate_user().login = "another"  # type: ignore[misc]


def test_department_membership_requires_same_department_and_organization() -> None:
    """Одинаковый ID отдела не даёт доступа к чужой организации."""
    membership = Membership(USER_ID, ORGANIZATION_ID, DEPARTMENT_ID)
    own_department = Department(DEPARTMENT_ID, ORGANIZATION_ID, "ПТО")
    foreign_department = Department(DEPARTMENT_ID, OTHER_ORGANIZATION_ID, "ПТО")

    assert membership.allows_department(own_department)
    assert not membership.allows_department(foreign_department)
    assert not replace(membership, active=False).allows_department(own_department)
    assert not membership.allows_department(replace(own_department, active=False))


def test_organization_membership_without_department_grants_no_department_scope() -> (
    None
):
    """Членство в организации само по себе не назначает отдел."""
    organization = Organization(ORGANIZATION_ID, "Компания")
    department = Department(DEPARTMENT_ID, organization.organization_id, "ПТО")

    assert not Membership(USER_ID, ORGANIZATION_ID).allows_department(department)


@pytest.mark.parametrize("model", [Organization, Department, Membership])
def test_organization_models_reject_invalid_active_type(model: type) -> None:
    """Строка из HTTP не подменяет булев флаг активности."""
    if model is Organization:
        values = (ORGANIZATION_ID, "Компания")
    elif model is Department:
        values = (DEPARTMENT_ID, ORGANIZATION_ID, "ПТО")
    else:
        values = (USER_ID, ORGANIZATION_ID, DEPARTMENT_ID)

    with pytest.raises(TypeError, match="active"):
        model(*values, active="false")
