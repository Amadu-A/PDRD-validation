# services/user-service/src/pdrd_user_service/infrastructure/database/repository.py

"""Асинхронное хранение пользователей с транзакционной сменой прав."""

import json
from datetime import datetime
from uuid import UUID, uuid4

from pdrd_user_service.application.ports.repository import (
    AuthorizationConflict,
    BootstrapAlreadyPerformed,
    IdentityConflict,
)
from pdrd_user_service.domain.access import AccessTier, Role
from pdrd_user_service.domain.identity import (
    Department,
    ExternalIdentity,
    Membership,
    Organization,
    UserAccount,
    UserKind,
    UserStatus,
)
from pdrd_user_service.domain.role_assignments import (
    RoleAssignment,
    RoleScope,
    RoleSource,
    ScopeKind,
)
from pdrd_user_service.infrastructure.database.models import (
    AdminBootstrapModel,
    CatalogSectionDistributionModel,
    DepartmentModel,
    ExternalIdentityModel,
    MembershipModel,
    NormativeAccessEventModel,
    OrganizationModel,
    ReviewAccessEventModel,
    RoleAssignmentEventModel,
    RoleAssignmentModel,
    SectionAccessEventModel,
    UserModel,
    UserSectionModel,
)
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


def _user_from_model(row: UserModel) -> UserAccount:
    """Восстанавливает проверяемую доменную модель профиля."""
    return UserAccount(
        user_id=row.user_id,
        kind=UserKind(row.kind),
        tier=AccessTier(row.tier),
        status=UserStatus(row.status),
        display_name=row.display_name,
        created_at=row.created_at,
        login=row.login,
        email=row.email,
        last_login_at=row.last_login_at,
        authorization_version=row.authorization_version,
        review_access_enabled=row.review_access_enabled,
        normative_access_enabled=row.normative_access_enabled,
    )


def _assignment_from_model(row: RoleAssignmentModel) -> RoleAssignment:
    """Восстанавливает назначение со всеми сроками и областью действия."""
    return RoleAssignment(
        assignment_id=row.assignment_id,
        user_id=row.user_id,
        role=Role(row.role),
        source=RoleSource(row.source),
        scope=RoleScope(
            kind=ScopeKind(row.scope_kind),
            organization_id=row.organization_id,
            department_id=row.department_id,
        ),
        created_at=row.created_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        external_group_id=row.external_group_id,
    )


def _assignment_to_model(assignment: RoleAssignment) -> RoleAssignmentModel:
    """Создаёт строку только из проверенной доменной модели."""
    return RoleAssignmentModel(
        assignment_id=assignment.assignment_id,
        user_id=assignment.user_id,
        role=assignment.role.value,
        source=assignment.source.value,
        scope_kind=assignment.scope.kind.value,
        organization_id=assignment.scope.organization_id,
        department_id=assignment.scope.department_id,
        created_at=assignment.created_at,
        expires_at=assignment.expires_at,
        revoked_at=assignment.revoked_at,
        external_group_id=assignment.external_group_id,
    )


class SqlAlchemyUserRepository:
    """Читает и изменяет записи строго в сессии своего Unit of Work."""

    def __init__(self, session: AsyncSession) -> None:
        """Получает сессию через DI, не открывая самостоятельных транзакций."""
        self._session = session

    async def get_user(
        self, user_id: UUID, *, for_update: bool = False
    ) -> UserAccount | None:
        """Возвращает профиль; блокировка защищает проверку прав перед записью."""
        statement = select(UserModel).where(UserModel.user_id == user_id)
        if for_update:
            statement = statement.with_for_update()
        row = await self._session.scalar(statement)
        return _user_from_model(row) if row is not None else None

    async def find_identity(
        self, provider_id: str, namespace: str, subject: str
    ) -> UserAccount | None:
        """Находит профиль по устойчивому ключу; email и login не участвуют."""
        row = await self._session.scalar(
            select(UserModel)
            .join(
                ExternalIdentityModel,
                ExternalIdentityModel.user_id == UserModel.user_id,
            )
            .where(
                ExternalIdentityModel.provider_id == provider_id,
                ExternalIdentityModel.namespace == namespace,
                ExternalIdentityModel.subject == subject,
            )
        )
        return _user_from_model(row) if row is not None else None

    async def find_by_login(self, login: str) -> UserAccount | None:
        """Локальный логин имеет приоритет; неоднозначный каталог закрывает вход."""
        rows = (
            await self._session.scalars(
                select(UserModel)
                .where(
                    or_(
                        func.lower(UserModel.login) == login,
                        func.lower(UserModel.email) == login,
                    )
                )
                .order_by((UserModel.kind == UserKind.LOCAL.value).desc())
                .limit(2)
            )
        ).all()
        if len(rows) > 1 and (
            rows[0].kind != UserKind.LOCAL.value or rows[1].kind == UserKind.LOCAL.value
        ):
            raise IdentityConflict("Логин относится к нескольким профилям")
        return _user_from_model(rows[0]) if rows else None

    async def list_users(
        self, *, limit: int, offset: int
    ) -> tuple[tuple[UserAccount, ...], int]:
        """Читает страницу в устойчивом порядке вместе с числом профилей."""
        total = await self._session.scalar(select(func.count()).select_from(UserModel))
        rows = (
            await self._session.scalars(
                select(UserModel)
                .order_by(UserModel.created_at.desc(), UserModel.user_id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return tuple(_user_from_model(row) for row in rows), int(total or 0)

    async def create_user(self, user: UserAccount, identity: ExternalIdentity) -> None:
        """Создаёт профиль и ключ входа в одной транзакции без сиротских строк."""
        if identity.user_id != user.user_id:
            raise ValueError("Идентичность относится к другому пользователю")
        try:
            async with self._session.begin_nested():
                self._session.add(
                    UserModel(
                        user_id=user.user_id,
                        kind=user.kind.value,
                        tier=user.tier.value,
                        status=user.status.value,
                        display_name=user.display_name,
                        login=user.login,
                        email=user.email,
                        created_at=user.created_at,
                        last_login_at=user.last_login_at,
                        authorization_version=user.authorization_version,
                        review_access_enabled=user.review_access_enabled,
                        normative_access_enabled=user.normative_access_enabled,
                    )
                )
                # Без ORM-связи SQLAlchemy может вставить identity раньше accounts.
                # Первая запись должна получить FK-цель в той же транзакции.
                await self._session.flush()
                self._session.add(
                    ExternalIdentityModel(
                        provider_id=identity.provider_id,
                        namespace=identity.namespace,
                        subject=identity.subject,
                        user_id=identity.user_id,
                    )
                )
                await self._session.flush()
        except IntegrityError as error:
            raise IdentityConflict("Профиль или идентичность уже существуют") from error

    async def initialize_access(
        self,
        updated_user: UserAccount,
        assignment: RoleAssignment,
        section_ids: tuple[UUID, ...],
        *,
        expected_authorization_version: int,
        activate_external: bool = False,
    ) -> None:
        """Сохраняет первичный доступ без промежуточного состояния и второго CAS."""
        if (
            assignment.user_id != updated_user.user_id
            or updated_user.kind not in {UserKind.CORPORATE, UserKind.EXTERNAL}
            or expected_authorization_version != 1
            or assignment.expires_at is not None
            or assignment.revoked_at is not None
            or assignment.role is not Role.DESIGNER
            or assignment.source is not RoleSource.LOCAL
            or assignment.scope.kind is not ScopeKind.OWN
            or updated_user.status is not UserStatus.ACTIVE
            or updated_user.tier is not AccessTier.MEMBER
            or updated_user.authorization_version != expected_authorization_version + 1
        ):
            raise ValueError("Некорректное первичное назначение доступа")
        async with self._session.begin_nested():
            current = await self.get_user(updated_user.user_id, for_update=True)
            if (
                current is None
                or current.authorization_version != expected_authorization_version
                or await self.list_assignments(updated_user.user_id, for_update=True)
                or await self.list_sections(updated_user.user_id)
            ):
                raise AuthorizationConflict("Первичный доступ уже назначен")
            if activate_external:
                if (
                    current.kind is not UserKind.EXTERNAL
                    or current.status is not UserStatus.PENDING_VERIFICATION
                ):
                    raise AuthorizationConflict(
                        "Профиль уже подтверждён или заблокирован"
                    )
                await self.activate_external(
                    updated_user, expected_authorization_version
                )
                await self._session.execute(
                    update(UserModel)
                    .where(UserModel.user_id == updated_user.user_id)
                    .values(tier=AccessTier.MEMBER.value)
                )
            else:
                if current.status is not UserStatus.ACTIVE:
                    raise AuthorizationConflict("Профиль не является активным")
                await self._bump_version(updated_user, expected_authorization_version)
                await self._session.execute(
                    update(UserModel)
                    .where(UserModel.user_id == updated_user.user_id)
                    .values(tier=AccessTier.MEMBER.value)
                )
            self._session.add(_assignment_to_model(assignment))
            await self._session.flush()
            self._session.add(
                RoleAssignmentEventModel(
                    event_id=uuid4(),
                    assignment_id=assignment.assignment_id,
                    actor_user_id=updated_user.user_id,
                    action="assign",
                    occurred_at=assignment.created_at,
                    authorization_version=updated_user.authorization_version,
                )
            )
            await self.replace_sections(
                updated_user.user_id,
                section_ids,
                actor_user_id=updated_user.user_id,
                authorization_version=updated_user.authorization_version,
                created_at=assignment.created_at,
            )
            await self._session.flush()

    async def activate_external(
        self, updated_user: UserAccount, expected_authorization_version: int
    ) -> None:
        """Атомарно переводит подтверждённый внешний профиль в активный."""
        if (
            updated_user.kind is not UserKind.EXTERNAL
            or updated_user.status is not UserStatus.ACTIVE
            or updated_user.authorization_version != expected_authorization_version + 1
        ):
            raise ValueError("Некорректный переход состояния внешнего профиля")
        changed = await self._session.scalar(
            update(UserModel)
            .where(
                UserModel.user_id == updated_user.user_id,
                UserModel.kind == UserKind.EXTERNAL.value,
                UserModel.status == UserStatus.PENDING_VERIFICATION.value,
                UserModel.authorization_version == expected_authorization_version,
            )
            .values(
                status=UserStatus.ACTIVE.value,
                authorization_version=updated_user.authorization_version,
            )
            .returning(UserModel.user_id)
        )
        if changed is None:
            raise AuthorizationConflict("Состояние профиля изменилось до подтверждения")

    async def get_organization(self, organization_id: UUID) -> Organization | None:
        """Находит организацию независимо от её активности."""
        row = await self._session.get(OrganizationModel, organization_id)
        if row is None:
            return None
        return Organization(row.organization_id, row.name, row.active)

    async def list_organizations(
        self, *, limit: int, offset: int
    ) -> tuple[tuple[Organization, ...], int]:
        """Читает страницу организаций в устойчивом порядке."""
        total = await self._session.scalar(
            select(func.count()).select_from(OrganizationModel)
        )
        rows = (
            await self._session.scalars(
                select(OrganizationModel)
                .order_by(OrganizationModel.name, OrganizationModel.organization_id)
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return (
            tuple(
                Organization(row.organization_id, row.name, row.active) for row in rows
            ),
            int(total or 0),
        )

    async def create_organization(self, organization: Organization) -> None:
        """Сохраняет границу организации в этой транзакции."""
        self._session.add(
            OrganizationModel(
                organization_id=organization.organization_id,
                name=organization.name,
                active=organization.active,
            )
        )
        await self._session.flush()

    async def get_department(
        self, organization_id: UUID, department_id: UUID
    ) -> Department | None:
        """Не позволяет выбрать отдел чужой организации."""
        row = await self._session.scalar(
            select(DepartmentModel).where(
                DepartmentModel.organization_id == organization_id,
                DepartmentModel.department_id == department_id,
            )
        )
        if row is None:
            return None
        return Department(row.department_id, row.organization_id, row.name, row.active)

    async def list_departments(
        self, organization_id: UUID, *, limit: int, offset: int
    ) -> tuple[tuple[Department, ...], int]:
        """Читает отделы только указанной организации."""
        total = await self._session.scalar(
            select(func.count())
            .select_from(DepartmentModel)
            .where(DepartmentModel.organization_id == organization_id)
        )
        rows = (
            await self._session.scalars(
                select(DepartmentModel)
                .where(DepartmentModel.organization_id == organization_id)
                .order_by(DepartmentModel.name, DepartmentModel.department_id)
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return (
            tuple(
                Department(row.department_id, row.organization_id, row.name, row.active)
                for row in rows
            ),
            int(total or 0),
        )

    async def create_department(self, department: Department) -> None:
        """Сохраняет отдел с FK на его организацию."""
        self._session.add(
            DepartmentModel(
                department_id=department.department_id,
                organization_id=department.organization_id,
                name=department.name,
                active=department.active,
            )
        )
        await self._session.flush()

    async def create_membership(self, membership: Membership) -> None:
        """Сохраняет членство с tenant FK на отдел."""
        self._session.add(
            MembershipModel(
                membership_id=uuid4(),
                user_id=membership.user_id,
                organization_id=membership.organization_id,
                department_id=membership.department_id,
                active=membership.active,
            )
        )
        await self._session.flush()

    async def get_department_membership(
        self, user_id: UUID, organization_id: UUID, department_id: UUID
    ) -> Membership | None:
        """Находит сохранённое членство без маскирования активности справочника."""
        row = await self._session.scalar(
            select(MembershipModel)
            .where(
                MembershipModel.user_id == user_id,
                MembershipModel.organization_id == organization_id,
                MembershipModel.department_id == department_id,
            )
            .with_for_update()
        )
        if row is None:
            return None
        return Membership(
            row.user_id, row.organization_id, row.department_id, row.active
        )

    async def set_department_membership(
        self,
        updated_user: UserAccount,
        membership: Membership,
        *,
        expected_authorization_version: int,
    ) -> None:
        """Фиксирует изменение членства вместе с CAS версии прав."""
        if (
            membership.user_id != updated_user.user_id
            or membership.department_id is None
        ):
            raise ValueError("Требуется членство в отделе целевого пользователя")
        await self._bump_version(updated_user, expected_authorization_version)
        existing = await self._session.scalar(
            select(MembershipModel)
            .where(
                MembershipModel.user_id == membership.user_id,
                MembershipModel.organization_id == membership.organization_id,
                MembershipModel.department_id == membership.department_id,
            )
            .with_for_update()
        )
        if existing is None:
            self._session.add(
                MembershipModel(
                    membership_id=uuid4(),
                    user_id=membership.user_id,
                    organization_id=membership.organization_id,
                    department_id=membership.department_id,
                    active=membership.active,
                )
            )
        else:
            existing.active = membership.active
        await self._session.flush()

    async def list_memberships(self, user_id: UUID) -> tuple[Membership, ...]:
        """Скрывает права через выключенные организации и отделы."""
        rows = (
            await self._session.execute(
                select(
                    MembershipModel, OrganizationModel.active, DepartmentModel.active
                )
                .join(
                    OrganizationModel,
                    OrganizationModel.organization_id
                    == MembershipModel.organization_id,
                )
                .outerjoin(
                    DepartmentModel,
                    and_(
                        DepartmentModel.department_id == MembershipModel.department_id,
                        DepartmentModel.organization_id
                        == MembershipModel.organization_id,
                    ),
                )
                .where(MembershipModel.user_id == user_id)
                .order_by(MembershipModel.organization_id)
            )
        ).all()
        return tuple(
            Membership(
                user_id=row.user_id,
                organization_id=row.organization_id,
                department_id=row.department_id,
                active=(
                    row.active
                    and organization_active
                    and (row.department_id is None or department_active is True)
                ),
            )
            for row, organization_active, department_active in rows
        )

    async def list_assignments(
        self, user_id: UUID, *, for_update: bool = False
    ) -> tuple[RoleAssignment, ...]:
        """Возвращает назначения в постоянном порядке; опционально блокирует."""
        statement = (
            select(RoleAssignmentModel)
            .where(RoleAssignmentModel.user_id == user_id)
            .order_by(RoleAssignmentModel.created_at, RoleAssignmentModel.assignment_id)
        )
        if for_update:
            statement = statement.with_for_update()
        rows = (await self._session.scalars(statement)).all()
        return tuple(_assignment_from_model(row) for row in rows)

    async def _bump_version(
        self,
        updated_user: UserAccount,
        expected_authorization_version: int,
        *,
        bootstrap: bool = False,
    ) -> None:
        """Сравнивает и меняет auth-version одним PostgreSQL UPDATE."""
        if updated_user.authorization_version != expected_authorization_version + 1:
            raise ValueError("Новая версия полномочий должна увеличиться на один")
        statement = (
            update(UserModel)
            .where(
                UserModel.user_id == updated_user.user_id,
                UserModel.authorization_version == expected_authorization_version,
            )
            .values(authorization_version=updated_user.authorization_version)
            .returning(UserModel.user_id)
        )
        if bootstrap:
            statement = statement.where(
                UserModel.kind.in_((UserKind.CORPORATE.value, UserKind.LOCAL.value)),
                UserModel.status == UserStatus.ACTIVE.value,
                UserModel.tier == AccessTier.MEMBER.value,
            )
        changed = await self._session.scalar(statement)
        if changed is None:
            raise AuthorizationConflict("Версия полномочий или профиль изменились")

    async def replace_review_access(
        self,
        updated_user: UserAccount,
        *,
        expected_authorization_version: int,
        actor_user_id: UUID,
        created_at: datetime,
    ) -> None:
        """Одной транзакцией сохраняет флаг, CAS-версию и событие аудита."""
        async with self._session.begin_nested():
            await self._bump_version(updated_user, expected_authorization_version)
            await self._session.execute(
                update(UserModel)
                .where(UserModel.user_id == updated_user.user_id)
                .values(review_access_enabled=updated_user.review_access_enabled)
            )
            self._session.add(
                ReviewAccessEventModel(
                    event_id=uuid4(),
                    user_id=updated_user.user_id,
                    actor_user_id=actor_user_id,
                    enabled=updated_user.review_access_enabled,
                    authorization_version=updated_user.authorization_version,
                    created_at=created_at,
                )
            )
            await self._session.flush()

    async def replace_normative_access(
        self,
        updated_user: UserAccount,
        *,
        expected_authorization_version: int,
        actor_user_id: UUID,
        created_at: datetime,
    ) -> None:
        """Одной транзакцией сохраняет флаг, CAS-версию и событие аудита."""
        async with self._session.begin_nested():
            await self._bump_version(updated_user, expected_authorization_version)
            await self._session.execute(
                update(UserModel)
                .where(UserModel.user_id == updated_user.user_id)
                .values(normative_access_enabled=updated_user.normative_access_enabled)
            )
            self._session.add(
                NormativeAccessEventModel(
                    event_id=uuid4(),
                    user_id=updated_user.user_id,
                    actor_user_id=actor_user_id,
                    enabled=updated_user.normative_access_enabled,
                    authorization_version=updated_user.authorization_version,
                    created_at=created_at,
                )
            )
            await self._session.flush()

    async def add_role_assignment(
        self,
        updated_user: UserAccount,
        assignment: RoleAssignment,
        expected_authorization_version: int,
        actor_user_id: UUID,
    ) -> None:
        """Сохраняет роль и новую версию совместно либо откатывает обе."""
        if assignment.user_id != updated_user.user_id:
            raise ValueError("Назначение относится к другому пользователю")
        if not isinstance(actor_user_id, UUID):
            raise TypeError("actor_user_id должен быть UUID")
        if assignment.role is Role.PLATFORM_ADMIN:
            raise ValueError("Первого администратора назначает bootstrap")
        if assignment.source is RoleSource.AD_GROUP:
            raise ValueError("AD-группы не синхронизируются в текущем этапе")
        async with self._session.begin_nested():
            await self._bump_version(updated_user, expected_authorization_version)
            self._session.add(_assignment_to_model(assignment))
            # Событие ссылается на назначение; порядок нужен даже без ORM-связи.
            await self._session.flush()
            self._session.add(
                RoleAssignmentEventModel(
                    event_id=uuid4(),
                    assignment_id=assignment.assignment_id,
                    actor_user_id=actor_user_id,
                    action="assign",
                    occurred_at=assignment.created_at,
                    authorization_version=updated_user.authorization_version,
                )
            )
            await self._session.flush()

    async def revoke_role_assignment(
        self,
        updated_user: UserAccount,
        assignment_id: UUID,
        revoked_at: datetime,
        expected_authorization_version: int,
        actor_user_id: UUID,
    ) -> None:
        """Отзывает действующую роль вместе с обновлением версии прав."""
        if not isinstance(actor_user_id, UUID):
            raise TypeError("actor_user_id должен быть UUID")
        async with self._session.begin_nested():
            await self._bump_version(updated_user, expected_authorization_version)
            changed = await self._session.scalar(
                update(RoleAssignmentModel)
                .where(
                    RoleAssignmentModel.assignment_id == assignment_id,
                    RoleAssignmentModel.user_id == updated_user.user_id,
                    RoleAssignmentModel.role != Role.PLATFORM_ADMIN.value,
                    RoleAssignmentModel.revoked_at.is_(None),
                    RoleAssignmentModel.created_at <= revoked_at,
                    or_(
                        RoleAssignmentModel.expires_at.is_(None),
                        RoleAssignmentModel.expires_at > revoked_at,
                    ),
                )
                .values(revoked_at=revoked_at)
                .returning(RoleAssignmentModel.assignment_id)
            )
            if changed is None:
                raise AuthorizationConflict("Действующее назначение не найдено")
            self._session.add(
                RoleAssignmentEventModel(
                    event_id=uuid4(),
                    assignment_id=assignment_id,
                    actor_user_id=actor_user_id,
                    action="revoke",
                    occurred_at=revoked_at,
                    authorization_version=updated_user.authorization_version,
                )
            )
            await self._session.flush()

    async def bootstrap_admin(
        self,
        updated_user: UserAccount,
        assignment: RoleAssignment,
        expected_authorization_version: int,
    ) -> None:
        """Единожды назначает первого администратора с DB singleton guard."""
        if (
            updated_user.kind not in {UserKind.CORPORATE, UserKind.LOCAL}
            or updated_user.status is not UserStatus.ACTIVE
            or updated_user.tier is not AccessTier.MEMBER
            or assignment.user_id != updated_user.user_id
            or assignment.role is not Role.PLATFORM_ADMIN
            or assignment.source is not RoleSource.LOCAL
            or assignment.scope.kind is not ScopeKind.PLATFORM
            or assignment.revoked_at is not None
            or assignment.expires_at is not None
        ):
            raise ValueError(
                "Первым администратором может стать активный сотрудник или локальный аккаунт"
            )
        try:
            async with self._session.begin_nested():
                existing = await self._session.scalar(
                    select(RoleAssignmentModel.assignment_id)
                    .join(UserModel, UserModel.user_id == RoleAssignmentModel.user_id)
                    .where(
                        RoleAssignmentModel.role == Role.PLATFORM_ADMIN.value,
                        RoleAssignmentModel.source == RoleSource.LOCAL.value,
                        RoleAssignmentModel.created_at <= assignment.created_at,
                        or_(
                            RoleAssignmentModel.revoked_at.is_(None),
                            RoleAssignmentModel.revoked_at > assignment.created_at,
                        ),
                        or_(
                            RoleAssignmentModel.expires_at.is_(None),
                            RoleAssignmentModel.expires_at > assignment.created_at,
                        ),
                        UserModel.status == UserStatus.ACTIVE.value,
                    )
                    .limit(1)
                )
                if existing is not None:
                    raise BootstrapAlreadyPerformed("Администратор уже существует")
                await self._bump_version(
                    updated_user, expected_authorization_version, bootstrap=True
                )
                self._session.add(_assignment_to_model(assignment))
                # Bootstrap и аудит содержат FK на новое назначение.
                await self._session.flush()
                self._session.add(
                    AdminBootstrapModel(
                        singleton_id=1,
                        user_id=updated_user.user_id,
                        assignment_id=assignment.assignment_id,
                        created_at=assignment.created_at,
                    )
                )
                self._session.add(
                    RoleAssignmentEventModel(
                        event_id=uuid4(),
                        assignment_id=assignment.assignment_id,
                        actor_user_id=None,
                        action="bootstrap",
                        occurred_at=assignment.created_at,
                        authorization_version=updated_user.authorization_version,
                    )
                )
                await self._session.flush()
        except IntegrityError as error:
            raise BootstrapAlreadyPerformed("Bootstrap уже выполнен") from error

    async def replace_worker_roles(
        self,
        updated_user: UserAccount,
        previous_assignment_ids: tuple[UUID, ...],
        new_assignment: RoleAssignment | None,
        revoked_at: datetime,
        expected_authorization_version: int,
        actor_user_id: UUID,
    ) -> None:
        """Одной транзакцией обновляет версию, роли и события аудита."""
        if not isinstance(actor_user_id, UUID):
            raise TypeError("actor_user_id должен быть UUID")
        if new_assignment is not None and (
            new_assignment.user_id != updated_user.user_id
            or new_assignment.role not in set(Role)
            or new_assignment.source is not RoleSource.LOCAL
        ):
            raise ValueError("Недопустимое новое назначение")
        if len(previous_assignment_ids) != len(set(previous_assignment_ids)):
            raise ValueError("Повторяющиеся назначения для отзыва")
        async with self._session.begin_nested():
            await self._bump_version(updated_user, expected_authorization_version)
            await self._session.execute(
                update(UserModel)
                .where(UserModel.user_id == updated_user.user_id)
                .values(tier=updated_user.tier.value)
            )
            if previous_assignment_ids:
                revoked = (
                    await self._session.scalars(
                        update(RoleAssignmentModel)
                        .where(
                            RoleAssignmentModel.user_id == updated_user.user_id,
                            RoleAssignmentModel.assignment_id.in_(
                                previous_assignment_ids
                            ),
                            RoleAssignmentModel.role.in_(
                                tuple(role.value for role in Role)
                            ),
                            RoleAssignmentModel.source == RoleSource.LOCAL.value,
                            RoleAssignmentModel.revoked_at.is_(None),
                        )
                        .values(revoked_at=revoked_at)
                        .returning(RoleAssignmentModel.assignment_id)
                    )
                ).all()
                if set(revoked) != set(previous_assignment_ids):
                    raise AuthorizationConflict("Назначения изменились до замены")
            if new_assignment is not None:
                self._session.add(_assignment_to_model(new_assignment))
                await self._session.flush()
            for assignment_id in previous_assignment_ids:
                self._session.add(
                    RoleAssignmentEventModel(
                        event_id=uuid4(),
                        assignment_id=assignment_id,
                        actor_user_id=actor_user_id,
                        action="revoke",
                        occurred_at=revoked_at,
                        authorization_version=updated_user.authorization_version,
                    )
                )
            if new_assignment is not None:
                self._session.add(
                    RoleAssignmentEventModel(
                        event_id=uuid4(),
                        assignment_id=new_assignment.assignment_id,
                        actor_user_id=actor_user_id,
                        action="assign",
                        occurred_at=revoked_at,
                        authorization_version=updated_user.authorization_version,
                    )
                )
            await self._session.flush()

    async def lock_section_catalog(self) -> None:
        """Берёт общий транзакционный PostgreSQL lock первичных назначений каталога."""
        await self._session.execute(
            select(func.pg_advisory_xact_lock(0x50445244534543))
        )

    async def list_distributed_sections(self) -> tuple[UUID, ...]:
        """Читает завершённые распределения из собственной таблицы."""
        return tuple(
            await self._session.scalars(
                select(CatalogSectionDistributionModel.section_id).order_by(
                    CatalogSectionDistributionModel.section_id
                )
            )
        )

    async def lock_catalog_users(self, actor_user_id: UUID) -> tuple[UserAccount, ...]:
        """Блокирует профили в общем UUID-порядке, включая неактивного актёра."""
        rows = (
            await self._session.scalars(
                select(UserModel)
                .where(
                    or_(
                        and_(
                            UserModel.status == UserStatus.ACTIVE.value,
                            UserModel.tier == AccessTier.MEMBER.value,
                        ),
                        UserModel.user_id == actor_user_id,
                    )
                )
                .order_by(UserModel.user_id)
                .with_for_update()
            )
        ).all()
        return tuple(_user_from_model(row) for row in rows)

    async def reserve_section_distribution(
        self, section_id: UUID, actor_user_id: UUID, created_at: datetime
    ) -> bool:
        """Вставляет marker атомарно; rollback разрешает повтор после частичного сбоя."""
        reserved = await self._session.scalar(
            insert(CatalogSectionDistributionModel)
            .values(
                section_id=section_id,
                actor_user_id=actor_user_id,
                created_at=created_at,
            )
            .on_conflict_do_nothing(
                index_elements=[CatalogSectionDistributionModel.section_id]
            )
            .returning(CatalogSectionDistributionModel.section_id)
        )
        return reserved is not None

    async def list_sections(self, user_id: UUID) -> tuple[UUID, ...]:
        """Читает назначенные разделы одним запросом в пределах транзакции."""
        result = await self._session.scalars(
            select(UserSectionModel.section_id)
            .where(UserSectionModel.user_id == user_id)
            .order_by(UserSectionModel.section_id)
        )
        return tuple(result.all())

    async def replace_sections(
        self,
        user_id: UUID,
        section_ids: tuple[UUID, ...],
        *,
        actor_user_id: UUID,
        authorization_version: int,
        created_at: datetime,
    ) -> None:
        """Заменяет набор и аудит в транзакции изменения роли и версии прав."""
        await self._session.execute(
            delete(UserSectionModel).where(UserSectionModel.user_id == user_id)
        )
        self._session.add_all(
            [
                UserSectionModel(user_id=user_id, section_id=section_id)
                for section_id in section_ids
            ]
        )
        self._session.add(
            SectionAccessEventModel(
                event_id=uuid4(),
                user_id=user_id,
                actor_user_id=actor_user_id,
                section_ids=json.dumps([str(item) for item in section_ids]),
                authorization_version=authorization_version,
                created_at=created_at,
            )
        )
        await self._session.flush()
