# services/user-service/src/pdrd_user_service/infrastructure/database/repository.py

"""Асинхронное хранение пользователей с транзакционной сменой прав."""

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
    DepartmentModel,
    ExternalIdentityModel,
    MembershipModel,
    OrganizationModel,
    RoleAssignmentEventModel,
    RoleAssignmentModel,
    UserModel,
)
from sqlalchemy import and_, or_, select, update
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
                    )
                )
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
                UserModel.kind == UserKind.CORPORATE.value,
                UserModel.status == UserStatus.ACTIVE.value,
                UserModel.tier == AccessTier.MEMBER.value,
            )
        changed = await self._session.scalar(statement)
        if changed is None:
            raise AuthorizationConflict("Версия полномочий или профиль изменились")

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
            updated_user.kind is not UserKind.CORPORATE
            or updated_user.status is not UserStatus.ACTIVE
            or updated_user.tier is not AccessTier.MEMBER
            or assignment.user_id != updated_user.user_id
            or assignment.role is not Role.PLATFORM_ADMIN
            or assignment.source is not RoleSource.LOCAL
            or assignment.scope.kind is not ScopeKind.PLATFORM
            or assignment.revoked_at is not None
            or assignment.expires_at is not None
        ):
            raise ValueError("Первым администратором может стать активный сотрудник")
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
