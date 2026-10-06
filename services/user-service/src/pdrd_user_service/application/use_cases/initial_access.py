# services/user-service/src/pdrd_user_service/application/use_cases/initial_access.py

"""Первичная роль и доступ, выдаваемые только после подтверждения личности."""

from dataclasses import replace
from datetime import datetime
from uuid import UUID, uuid4

from pdrd_user_service.application.ports.repository import UserRepository
from pdrd_user_service.application.ports.section_catalog import (
    SectionCatalog,
    SectionCatalogUnavailable,
)
from pdrd_user_service.domain.access import AccessTier, Role
from pdrd_user_service.domain.identity import UserAccount, UserStatus
from pdrd_user_service.domain.role_assignments import (
    RoleAssignment,
    RoleScope,
    RoleSource,
    ScopeKind,
)


class InitialUserAccess:
    """Назначает проектировщика и снимок разделов одной версией полномочий."""

    def __init__(self, section_catalog: SectionCatalog | None) -> None:
        """Получает каталог через порт; отсутствие конфигурации запрещает создание."""
        self._section_catalog = section_catalog

    async def read_sections(self) -> tuple[UUID, ...]:
        """Получает каталог до начала транзакции изменения профиля."""
        if self._section_catalog is None:
            raise SectionCatalogUnavailable("Каталог разделов не настроен")
        sections = await self._section_catalog.list_section_ids()
        if any(not isinstance(section_id, UUID) for section_id in sections):
            raise SectionCatalogUnavailable("Некорректный каталог разделов")
        return tuple(sorted(set(sections), key=str))

    async def initialize(
        self,
        repository: UserRepository,
        user: UserAccount,
        section_ids: tuple[UUID, ...],
        now: datetime,
        *,
        activate_external: bool = False,
    ) -> UserAccount:
        """Сохраняет роль, реальные чекбоксы и аудит в транзакции создания/активации."""
        await repository.lock_section_catalog()
        distributed = await repository.list_distributed_sections()
        section_ids = tuple(sorted({*section_ids, *distributed}, key=str))
        updated = replace(
            user,
            tier=AccessTier.MEMBER,
            status=UserStatus.ACTIVE,
            authorization_version=user.authorization_version + 1,
        )
        assignment = RoleAssignment(
            assignment_id=uuid4(),
            user_id=user.user_id,
            role=Role.DESIGNER,
            source=RoleSource.LOCAL,
            scope=RoleScope(ScopeKind.OWN),
            created_at=now,
        )
        await repository.initialize_access(
            updated,
            assignment,
            section_ids,
            expected_authorization_version=user.authorization_version,
            activate_external=activate_external,
        )
        return updated
