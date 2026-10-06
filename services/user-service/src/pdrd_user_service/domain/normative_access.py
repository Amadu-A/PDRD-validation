# services/user-service/src/pdrd_user_service/domain/normative_access.py

"""Вычисляет доступ к удалению нормативных объектов из живых ролей и отдельного назначения."""

from dataclasses import dataclass

from pdrd_user_service.domain.access import (
    AccessSubject,
    AccessTier,
    Permission,
    Role,
    effective_permissions,
)


@dataclass(frozen=True, slots=True)
class NormativeAccessState:
    """Разделяет действующий доступ, автоматическое право и возможность назначения."""

    normative_access: bool
    normative_access_automatic: bool
    normative_access_editable: bool


def normative_access_state(subject: AccessSubject) -> NormativeAccessState:
    """Даёт автоматическое право админам, ручное — проектировщику или руководителю."""
    eligible = subject.active and subject.tier is AccessTier.MEMBER
    automatic = eligible and bool(subject.roles & {Role.PLATFORM_ADMIN})
    return NormativeAccessState(
        normative_access=Permission.NORMATIVE_DELETE in effective_permissions(subject),
        normative_access_automatic=automatic,
        normative_access_editable=eligible
        and bool(subject.roles & {Role.DESIGNER, Role.DEPARTMENT_HEAD})
        and not automatic,
    )
