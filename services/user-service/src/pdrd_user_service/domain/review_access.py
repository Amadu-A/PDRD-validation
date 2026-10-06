# services/user-service/src/pdrd_user_service/domain/review_access.py

"""Вычисляет доступ к ревью из живых ролей и отдельного назначения."""

from dataclasses import dataclass

from pdrd_user_service.domain.access import (
    AccessSubject,
    AccessTier,
    Permission,
    Role,
    effective_permissions,
)


@dataclass(frozen=True, slots=True)
class ReviewAccessState:
    """Разделяет действующий доступ, автоматическое право и возможность назначения."""

    review_access: bool
    review_access_automatic: bool
    review_access_editable: bool


def review_access_state(subject: AccessSubject) -> ReviewAccessState:
    """Даёт автоматическое право руководителям и админам, ручное — проектировщику."""
    eligible = subject.active and subject.tier is AccessTier.MEMBER
    automatic = eligible and bool(
        subject.roles & {Role.DEPARTMENT_HEAD, Role.PLATFORM_ADMIN}
    )
    return ReviewAccessState(
        review_access=Permission.REVIEW_OWN_READ in effective_permissions(subject),
        review_access_automatic=automatic,
        review_access_editable=eligible
        and Role.DESIGNER in subject.roles
        and not automatic,
    )
