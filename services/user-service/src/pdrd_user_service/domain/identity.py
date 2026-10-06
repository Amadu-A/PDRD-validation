# services/user-service/src/pdrd_user_service/domain/identity.py

"""Профили, устойчивые идентичности и организационная принадлежность PDRD.

Пароли и их хеши принадлежат auth-service. Логин и email можно менять:
сопоставление аккаунта с источником входа выполняется только по stable_key.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pdrd_user_service.domain.access import AccessTier


class UserKind(StrEnum):
    """Различает корпоративный, внешний и локальный аккаунты."""

    CORPORATE = "corporate"
    EXTERNAL = "external"
    LOCAL = "local"


class UserStatus(StrEnum):
    """Отражает состояние профиля, отличное от срока действия сессии."""

    PENDING_VERIFICATION = "pending_verification"
    ACTIVE = "active"
    BLOCKED = "blocked"


def _require_uuid(value: UUID, field_name: str) -> None:
    """Проверяет внутренний UUID без неявного преобразования из HTTP-строки."""
    if not isinstance(value, UUID):
        raise TypeError(f"{field_name} должен быть UUID")


def _require_nonblank(value: str, field_name: str) -> None:
    """Отклоняет пустой текст, сохраняя исходный устойчивый идентификатор."""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} должен быть строкой")
    if not value.strip():
        raise ValueError(f"{field_name} не должен быть пустым")


def _require_optional_nonblank(value: str | None, field_name: str) -> None:
    """Проверяет необязательный отображаемый атрибут профиля."""
    if value is not None:
        _require_nonblank(value, field_name)


def _require_aware_datetime(value: datetime, field_name: str) -> None:
    """Требует дату с часовым поясом для сравнения событий аккаунта."""
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} должен быть datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} должен содержать timezone")


@dataclass(frozen=True, slots=True)
class UserAccount:
    """Хранит профиль PDRD без корпоративного или локального пароля."""

    user_id: UUID
    kind: UserKind
    tier: AccessTier
    status: UserStatus
    display_name: str
    created_at: datetime
    login: str | None = None
    email: str | None = None
    last_login_at: datetime | None = None
    authorization_version: int = 1
    review_access_enabled: bool = False

    def __post_init__(self) -> None:
        """Проверяет профиль, способ регистрации и хронологию входов."""
        _require_uuid(self.user_id, "user_id")
        if not isinstance(self.review_access_enabled, bool):
            raise TypeError("review_access_enabled должен быть bool")
        if not isinstance(self.kind, UserKind):
            raise TypeError("kind должен быть UserKind")
        if not isinstance(self.tier, AccessTier):
            raise TypeError("tier должен быть AccessTier")
        if not isinstance(self.status, UserStatus):
            raise TypeError("status должен быть UserStatus")
        _require_nonblank(self.display_name, "display_name")
        _require_aware_datetime(self.created_at, "created_at")
        _require_optional_nonblank(self.login, "login")
        _require_optional_nonblank(self.email, "email")
        if self.last_login_at is not None:
            _require_aware_datetime(self.last_login_at, "last_login_at")
            if self.last_login_at < self.created_at:
                raise ValueError("last_login_at не может быть раньше created_at")
        if self.kind is UserKind.LOCAL and (
            self.login is None or self.status is UserStatus.PENDING_VERIFICATION
        ):
            raise ValueError("Локальному аккаунту нужен login без email-подтверждения")
        if self.tier is AccessTier.GUEST:
            raise ValueError("Гость не хранится как пользователь")
        if (
            self.kind is UserKind.CORPORATE
            and self.status is UserStatus.PENDING_VERIFICATION
        ):
            raise ValueError("Корпоративный аккаунт не ожидает email-подтверждения")
        if (
            self.kind is UserKind.CORPORATE
            and self.status is UserStatus.ACTIVE
            and self.login is None
        ):
            raise ValueError("Активному корпоративному аккаунту необходим login")
        if (
            self.kind is UserKind.EXTERNAL
            and self.status in {UserStatus.PENDING_VERIFICATION, UserStatus.ACTIVE}
            and self.email is None
        ):
            raise ValueError("Внешнему аккаунту для регистрации необходим email")
        if (
            self.status is UserStatus.PENDING_VERIFICATION
            and self.last_login_at is not None
        ):
            raise ValueError(
                "Ожидающий подтверждения аккаунт не может иметь last_login_at"
            )
        if not isinstance(self.authorization_version, int) or isinstance(
            self.authorization_version, bool
        ):
            raise TypeError("authorization_version должен быть целым числом")
        if self.authorization_version < 1:
            raise ValueError("authorization_version должен быть не меньше 1")


@dataclass(frozen=True, slots=True)
class ExternalIdentity:
    """Связывает подтверждённую auth-service идентичность с UUID PDRD."""

    provider_id: str
    namespace: str
    subject: str
    user_id: UUID

    def __post_init__(self) -> None:
        """Требует полный стабильный составной ключ провайдера."""
        _require_nonblank(self.provider_id, "provider_id")
        _require_nonblank(self.namespace, "namespace")
        _require_nonblank(self.subject, "subject")
        _require_uuid(self.user_id, "user_id")

    @property
    def stable_key(self) -> tuple[str, str, str]:
        """Возвращает ключ поиска, который не зависит от логина и email."""
        return (self.provider_id, self.namespace, self.subject)


@dataclass(frozen=True, slots=True)
class Organization:
    """Представляет границу данных отдельной организации."""

    organization_id: UUID
    name: str
    active: bool = True

    def __post_init__(self) -> None:
        """Проверяет пригодность организации для хранения и поиска."""
        _require_uuid(self.organization_id, "organization_id")
        _require_nonblank(self.name, "name")
        if not isinstance(self.active, bool):
            raise TypeError("active должен быть bool")


@dataclass(frozen=True, slots=True)
class Department:
    """Связывает отдел с единственной организацией."""

    department_id: UUID
    organization_id: UUID
    name: str
    active: bool = True

    def __post_init__(self) -> None:
        """Проверяет идентификаторы и название отдела."""
        _require_uuid(self.department_id, "department_id")
        _require_uuid(self.organization_id, "organization_id")
        _require_nonblank(self.name, "name")
        if not isinstance(self.active, bool):
            raise TypeError("active должен быть bool")


@dataclass(frozen=True, slots=True)
class Membership:
    """Описывает членство пользователя в организации и, возможно, отделе."""

    user_id: UUID
    organization_id: UUID
    department_id: UUID | None = None
    active: bool = True

    def __post_init__(self) -> None:
        """Проверяет форму членства; связь отдела проверяется с Department."""
        _require_uuid(self.user_id, "user_id")
        _require_uuid(self.organization_id, "organization_id")
        if self.department_id is not None:
            _require_uuid(self.department_id, "department_id")
        if not isinstance(self.active, bool):
            raise TypeError("active должен быть bool")

    def allows_department(self, department: Department) -> bool:
        """Проверяет членство в отделе той же организации."""
        if not isinstance(department, Department):
            raise TypeError("department должен быть Department")
        return (
            self.active
            and department.active
            and self.organization_id == department.organization_id
            and self.department_id == department.department_id
        )
