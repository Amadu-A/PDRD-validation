# services/user-service/src/pdrd_user_service/domain/access.py

"""Права операций PDRD для гостя, бесплатного аккаунта и рабочих ролей.

Это только первый уровень проверки: чтение конкретного задания или документа
дополнительно требует проверки владельца, организации и, для гостя,
действующего временного секрета результата. Transport должен получать роли
только из доверенного user-service, а не из HTTP-полей браузера.
"""

from dataclasses import dataclass, field
from enum import StrEnum


class AccessTier(StrEnum):
    """Определяет подтверждённый уровень аккаунта без смешения с ролью."""

    GUEST = "guest"
    REGISTERED_FREE = "registered_free"
    MEMBER = "member"


class Role(StrEnum):
    """Определяет прикладную роль действующего участника PDRD."""

    DESIGNER = "designer"
    DEPARTMENT_HEAD = "department_head"
    PLATFORM_ADMIN = "platform_admin"


class Permission(StrEnum):
    """Перечисляет разрешённые операции без произвольного SQL-доступа."""

    ANALYSIS_INPUT_UPLOAD = "analysis.input.upload"
    ANALYSIS_RUN = "analysis.run"
    ANALYSIS_RESULT_READ = "analysis.result.read"
    ANALYSIS_RESULT_DOWNLOAD = "analysis.result.download"
    PROFILE_READ = "profile.read"
    PROFILE_UPDATE = "profile.update"
    USER_DOCUMENT_OWN_READ = "user_documents.own.read"
    USER_DOCUMENT_OWN_WRITE = "user_documents.own.write"
    USER_DOCUMENT_SCOPED_READ = "user_documents.scoped.read"
    USER_DOCUMENT_SCOPED_WRITE = "user_documents.scoped.write"
    REVIEW_OWN_READ = "review.own.read"
    REVIEW_SCOPED_READ = "review.scoped.read"
    REVIEW_GOLD_CREATE = "review.gold.create"
    REVIEW_FINDINGS_DECIDE = "review.findings.decide"
    REVIEW_APPROVE = "review.approve"
    REVIEWED_PDF_DOWNLOAD = "review.pdf.download"
    NORMATIVE_CATALOG_READ = "normative.catalog.read"
    NORMATIVE_WRITE = "normative.write"
    NORMATIVE_DELETE = "normative.delete"
    EXPERIENCE_CATALOG_READ = "experience.catalog.read"
    EXPERIENCE_CAPTURE = "experience.capture"
    EXPERIENCE_VERSION_CREATE = "experience.version.create"
    EXPERIENCE_VERSION_APPLY = "experience.version.apply"
    WORKING_PROMPT_USE = "working_prompt.use"
    SYSTEM_PROMPT_READ = "system_prompt.read"
    SYSTEM_PROMPT_MANAGE = "system_prompt.manage"
    ADMIN_ACCESS = "admin.access"
    USERS_ROLES_ASSIGN = "users.roles.assign"
    SESSIONS_REVOKE = "sessions.revoke"
    REPORTS_READ = "reports.read"
    AUDIT_READ = "audit.read"


@dataclass(frozen=True, slots=True)
class AccessSubject:
    """Хранит доверенный уровень аккаунта, роли и статус активности.

    Роли заполняет только сервер после проверки сессии и user-service. Гость
    и бесплатный аккаунт не могут получить роли через этот контракт.
    """

    tier: AccessTier
    roles: frozenset[Role] = field(default_factory=frozenset)
    active: bool = True

    def __post_init__(self) -> None:
        """Отвергает некорректный или изменяемый доверенный контекст."""
        if not isinstance(self.tier, AccessTier):
            raise TypeError("tier должен быть AccessTier")
        if not isinstance(self.roles, frozenset):
            raise TypeError("roles должен быть frozenset")
        if any(not isinstance(role, Role) for role in self.roles):
            raise TypeError("roles должен содержать только Role")
        if not isinstance(self.active, bool):
            raise TypeError("active должен быть bool")
        if self.tier is not AccessTier.MEMBER and self.roles:
            raise ValueError("Роли доступны только действующему участнику")


_PUBLIC_PERMISSIONS = frozenset(
    {
        Permission.ANALYSIS_INPUT_UPLOAD,
        Permission.ANALYSIS_RUN,
        Permission.ANALYSIS_RESULT_READ,
        Permission.ANALYSIS_RESULT_DOWNLOAD,
        Permission.NORMATIVE_CATALOG_READ,
    }
)

_PROFILE_PERMISSIONS = frozenset(
    {
        Permission.PROFILE_READ,
        Permission.PROFILE_UPDATE,
        Permission.USER_DOCUMENT_OWN_READ,
        Permission.USER_DOCUMENT_OWN_WRITE,
        Permission.WORKING_PROMPT_USE,
        Permission.SYSTEM_PROMPT_READ,
    }
)

_DESIGNER_PERMISSIONS = frozenset(
    {
        Permission.USER_DOCUMENT_OWN_READ,
        Permission.USER_DOCUMENT_OWN_WRITE,
        Permission.REVIEW_OWN_READ,
        Permission.REVIEW_GOLD_CREATE,
    }
)

_HEAD_PERMISSIONS = frozenset(
    {
        *_DESIGNER_PERMISSIONS,
        Permission.USER_DOCUMENT_SCOPED_READ,
        Permission.USER_DOCUMENT_SCOPED_WRITE,
        Permission.REVIEW_SCOPED_READ,
        Permission.REVIEW_FINDINGS_DECIDE,
        Permission.REVIEW_APPROVE,
        Permission.REVIEWED_PDF_DOWNLOAD,
        Permission.NORMATIVE_WRITE,
        Permission.NORMATIVE_DELETE,
        Permission.EXPERIENCE_CATALOG_READ,
        Permission.EXPERIENCE_CAPTURE,
    }
)

_ADMIN_PERMISSIONS = frozenset(
    {
        *_PUBLIC_PERMISSIONS,
        *_PROFILE_PERMISSIONS,
        *_HEAD_PERMISSIONS,
        Permission.EXPERIENCE_VERSION_CREATE,
        Permission.EXPERIENCE_VERSION_APPLY,
        Permission.SYSTEM_PROMPT_MANAGE,
        Permission.ADMIN_ACCESS,
        Permission.USERS_ROLES_ASSIGN,
        Permission.SESSIONS_REVOKE,
        Permission.REPORTS_READ,
        Permission.AUDIT_READ,
    }
)

_ROLE_PERMISSIONS = {
    Role.DESIGNER: _DESIGNER_PERMISSIONS,
    Role.DEPARTMENT_HEAD: _HEAD_PERMISSIONS,
    Role.PLATFORM_ADMIN: _ADMIN_PERMISSIONS,
}


def effective_permissions(subject: AccessSubject) -> frozenset[Permission]:
    """Возвращает операции субъекта без проверки доступа к конкретному объекту.

    Заблокированный аккаунт не получает даже публичные операции в рамках
    текущей сессии. Участник без роли сохраняет только бесплатные операции.
    """
    if not isinstance(subject, AccessSubject):
        raise TypeError("subject должен быть AccessSubject")
    if not subject.active:
        return frozenset()

    permissions = set(_PUBLIC_PERMISSIONS)
    if subject.tier is not AccessTier.GUEST:
        permissions.update(_PROFILE_PERMISSIONS)
    if subject.tier is AccessTier.MEMBER:
        for role in subject.roles:
            permissions.update(_ROLE_PERMISSIONS[role])
    return frozenset(permissions)


def has_operation_permission(subject: AccessSubject, permission: Permission) -> bool:
    """Проверяет операцию; права на объект и временную ссылку проверяют отдельно."""
    if not isinstance(permission, Permission):
        raise TypeError("permission должен быть Permission")
    return permission in effective_permissions(subject)
