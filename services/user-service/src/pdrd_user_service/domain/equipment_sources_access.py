# services/user-service/src/pdrd_user_service/domain/equipment_sources_access.py

"""Вычисляет доступ к управлению источниками оборудования из живых ролей и отдельного назначения."""

from dataclasses import dataclass

from pdrd_user_service.domain.access import (
    AccessSubject,
    AccessTier,
    Permission,
    Role,
    effective_permissions,
)


@dataclass(frozen=True, slots=True)
class EquipmentSourcesAccessState:
    """Разделяет действующий доступ, автоматическое право и возможность назначения."""

    equipment_sources_access: bool
    equipment_sources_access_automatic: bool
    equipment_sources_access_editable: bool


def equipment_sources_access_state(
    subject: AccessSubject,
) -> EquipmentSourcesAccessState:
    """Даёт автоматическое право админам, ручное — пользователю."""
    eligible = subject.active and subject.tier is not AccessTier.GUEST
    automatic = eligible and bool(subject.roles & {Role.PLATFORM_ADMIN})
    return EquipmentSourcesAccessState(
        equipment_sources_access=Permission.EQUIPMENT_SOURCES_MANAGE
        in effective_permissions(subject),
        equipment_sources_access_automatic=automatic,
        equipment_sources_access_editable=eligible and not automatic,
    )
