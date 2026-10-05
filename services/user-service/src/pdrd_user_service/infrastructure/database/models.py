# services/user-service/src/pdrd_user_service/infrastructure/database/models.py

"""ORM-модели собственных профилей, идентичностей и назначений User Service."""

from datetime import datetime
from uuid import UUID

from pdrd_user_service.infrastructure.database.base import Base
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column


class UserModel(Base):
    """Профиль без пароля, хеша пароля и секрета сессии."""

    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('corporate', 'external', 'local')", name="ck_accounts_kind"
        ),
        CheckConstraint(
            "tier IN ('registered_free', 'member')", name="ck_accounts_tier"
        ),
        CheckConstraint(
            "status IN ('pending_verification', 'active', 'blocked')",
            name="ck_accounts_status",
        ),
        CheckConstraint(
            "kind <> 'local' OR (login IS NOT NULL AND status <> 'pending_verification')",
            name="ck_accounts_local_login",
        ),
        CheckConstraint("length(btrim(display_name)) > 0", name="ck_accounts_name"),
        CheckConstraint(
            "login IS NULL OR length(btrim(login)) > 0", name="ck_accounts_login"
        ),
        CheckConstraint(
            "email IS NULL OR length(btrim(email)) > 0", name="ck_accounts_email"
        ),
        CheckConstraint(
            "NOT (kind = 'corporate' AND status = 'pending_verification')",
            name="ck_accounts_corporate_pending",
        ),
        CheckConstraint(
            "kind <> 'corporate' OR status <> 'active' OR login IS NOT NULL",
            name="ck_accounts_corporate_login",
        ),
        CheckConstraint(
            "kind <> 'external' OR status = 'blocked' OR email IS NOT NULL",
            name="ck_accounts_external_email",
        ),
        CheckConstraint(
            "status <> 'pending_verification' OR last_login_at IS NULL",
            name="ck_accounts_pending_login",
        ),
        CheckConstraint(
            "last_login_at IS NULL OR last_login_at >= created_at",
            name="ck_accounts_login_chronology",
        ),
        CheckConstraint(
            "authorization_version >= 1", name="ck_accounts_authorization_version"
        ),
        {"schema": "users"},
    )

    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    tier: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    login: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(320))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    authorization_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )


class ExternalIdentityModel(Base):
    """Устойчивый составной ключ провайдера, независимо от email и login."""

    __tablename__ = "external_identities"
    __table_args__ = (
        PrimaryKeyConstraint(
            "provider_id", "namespace", "subject", name="pk_external_identities"
        ),
        CheckConstraint(
            "length(btrim(provider_id)) > 0", name="ck_external_identities_provider"
        ),
        CheckConstraint(
            "length(btrim(namespace)) > 0", name="ck_external_identities_namespace"
        ),
        CheckConstraint(
            "length(btrim(subject)) > 0", name="ck_external_identities_subject"
        ),
        Index("ix_external_identities_user_id", "user_id"),
        {"schema": "users"},
    )

    provider_id: Mapped[str] = mapped_column(String(64), nullable=False)
    namespace: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(512), nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.accounts.user_id", ondelete="CASCADE")
    )


class OrganizationModel(Base):
    """Граница доступа к данным одной организации."""

    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint("length(btrim(name)) > 0", name="ck_organizations_name"),
        {"schema": "users"},
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )


class DepartmentModel(Base):
    """Отдел одной организации с составным ключом для tenant FK."""

    __tablename__ = "departments"
    __table_args__ = (
        UniqueConstraint(
            "department_id", "organization_id", name="uq_departments_id_organization"
        ),
        CheckConstraint("length(btrim(name)) > 0", name="ck_departments_name"),
        {"schema": "users"},
    )

    department_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.organizations.organization_id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )


class MembershipModel(Base):
    """Членство пользователя в организации и необязательном отделе."""

    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "organization_id",
            "department_id",
            name="uq_memberships_user_organization_department",
        ),
        Index(
            "uq_memberships_user_organization_no_department",
            "user_id",
            "organization_id",
            unique=True,
            postgresql_where=text("department_id IS NULL"),
        ),
        ForeignKeyConstraint(
            ["department_id", "organization_id"],
            ["users.departments.department_id", "users.departments.organization_id"],
            name="fk_memberships_department_tenant",
            ondelete="RESTRICT",
        ),
        {"schema": "users"},
    )

    membership_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.accounts.user_id", ondelete="CASCADE")
    )
    organization_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.organizations.organization_id", ondelete="RESTRICT"),
    )
    department_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )


class RoleAssignmentModel(Base):
    """Назначение роли и история отзыва без хранения паролей AD."""

    __tablename__ = "role_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["department_id", "organization_id"],
            ["users.departments.department_id", "users.departments.organization_id"],
            name="fk_role_assignments_department_tenant",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "role IN ('designer', 'department_head', 'platform_admin')",
            name="ck_role_assignments_role",
        ),
        CheckConstraint(
            "source IN ('local', 'ad_group')", name="ck_role_assignments_source"
        ),
        CheckConstraint(
            "scope_kind IN ('platform', 'organization', 'department', 'own')",
            name="ck_role_assignments_scope_kind",
        ),
        CheckConstraint(
            "(scope_kind IN ('platform', 'own') AND organization_id IS NULL "
            "AND department_id IS NULL) OR "
            "(scope_kind = 'organization' AND organization_id IS NOT NULL "
            "AND department_id IS NULL) OR "
            "(scope_kind = 'department' AND organization_id IS NOT NULL "
            "AND department_id IS NOT NULL)",
            name="ck_role_assignments_scope",
        ),
        CheckConstraint(
            "(source = 'local' AND external_group_id IS NULL) OR "
            "(source = 'ad_group' AND external_group_id IS NOT NULL "
            "AND length(btrim(external_group_id)) > 0)",
            name="ck_role_assignments_group",
        ),
        CheckConstraint(
            "role <> 'platform_admin' OR "
            "(scope_kind = 'platform' AND source = 'local')",
            name="ck_role_assignments_admin_scope",
        ),
        CheckConstraint(
            "role <> 'department_head' OR scope_kind = 'department'",
            name="ck_role_assignments_head_scope",
        ),
        CheckConstraint(
            "role <> 'designer' OR scope_kind = 'own'",
            name="ck_role_assignments_designer_scope",
        ),
        CheckConstraint(
            "expires_at IS NULL OR expires_at > created_at",
            name="ck_role_assignments_expiry",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_role_assignments_revoke_time",
        ),
        Index("ix_role_assignments_user_id", "user_id"),
        {"schema": "users"},
    )

    assignment_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.accounts.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    organization_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.organizations.organization_id", ondelete="RESTRICT"),
    )
    department_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    external_group_id: Mapped[str | None] = mapped_column(String(512))


class AdminBootstrapModel(Base):
    """Единственная запись успешного первоначального назначения администратора."""

    __tablename__ = "admin_bootstrap"
    __table_args__ = (
        CheckConstraint("singleton_id = 1", name="ck_admin_bootstrap_singleton"),
        {"schema": "users"},
    )

    singleton_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.accounts.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    assignment_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.role_assignments.assignment_id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class RoleAssignmentEventModel(Base):
    """Запись аудита; репозиторий добавляет её вместе со сменой роли."""

    __tablename__ = "role_assignment_events"
    __table_args__ = (
        UniqueConstraint(
            "assignment_id", "action", name="uq_role_assignment_events_action"
        ),
        CheckConstraint(
            "action IN ('assign', 'revoke', 'bootstrap')",
            name="ck_role_assignment_events_action",
        ),
        CheckConstraint(
            "(action = 'bootstrap' AND actor_user_id IS NULL) OR "
            "(action IN ('assign', 'revoke') AND actor_user_id IS NOT NULL)",
            name="ck_role_assignment_events_actor",
        ),
        CheckConstraint(
            "authorization_version >= 1", name="ck_role_assignment_events_version"
        ),
        Index("ix_role_assignment_events_actor", "actor_user_id"),
        {"schema": "users"},
    )

    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    assignment_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.role_assignments.assignment_id", ondelete="RESTRICT"),
        nullable=False,
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.accounts.user_id", ondelete="RESTRICT"),
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    authorization_version: Mapped[int] = mapped_column(Integer, nullable=False)
