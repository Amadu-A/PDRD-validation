# services/user-service/alembic/versions/20260930_0001_create_users.py

"""Создаёт собственную схему профилей, членства и назначений PDRD.

Revision ID: 20260930_0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20260930_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Создаёт только таблицы users, не затрагивая другие сервисы."""
    op.execute("CREATE SCHEMA IF NOT EXISTS users")

    op.create_table(
        "accounts",
        sa.Column("user_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("tier", sa.String(24), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("login", sa.String(255)),
        sa.Column("email", sa.String(320)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column(
            "authorization_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.CheckConstraint(
            "kind IN ('corporate', 'external')", name="ck_accounts_kind"
        ),
        sa.CheckConstraint(
            "tier IN ('registered_free', 'member')", name="ck_accounts_tier"
        ),
        sa.CheckConstraint(
            "status IN ('pending_verification', 'active', 'blocked')",
            name="ck_accounts_status",
        ),
        sa.CheckConstraint("length(btrim(display_name)) > 0", name="ck_accounts_name"),
        sa.CheckConstraint(
            "login IS NULL OR length(btrim(login)) > 0", name="ck_accounts_login"
        ),
        sa.CheckConstraint(
            "email IS NULL OR length(btrim(email)) > 0", name="ck_accounts_email"
        ),
        sa.CheckConstraint(
            "NOT (kind = 'corporate' AND status = 'pending_verification')",
            name="ck_accounts_corporate_pending",
        ),
        sa.CheckConstraint(
            "kind <> 'corporate' OR status <> 'active' OR login IS NOT NULL",
            name="ck_accounts_corporate_login",
        ),
        sa.CheckConstraint(
            "kind <> 'external' OR status = 'blocked' OR email IS NOT NULL",
            name="ck_accounts_external_email",
        ),
        sa.CheckConstraint(
            "status <> 'pending_verification' OR last_login_at IS NULL",
            name="ck_accounts_pending_login",
        ),
        sa.CheckConstraint(
            "last_login_at IS NULL OR last_login_at >= created_at",
            name="ck_accounts_login_chronology",
        ),
        sa.CheckConstraint(
            "authorization_version >= 1", name="ck_accounts_authorization_version"
        ),
        schema="users",
    )
    op.create_table(
        "external_identities",
        sa.Column("provider_id", sa.String(64), nullable=False),
        sa.Column("namespace", sa.String(255), nullable=False),
        sa.Column("subject", sa.String(512), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "provider_id", "namespace", "subject", name="pk_external_identities"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.accounts.user_id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "length(btrim(provider_id)) > 0", name="ck_external_identities_provider"
        ),
        sa.CheckConstraint(
            "length(btrim(namespace)) > 0", name="ck_external_identities_namespace"
        ),
        sa.CheckConstraint(
            "length(btrim(subject)) > 0", name="ck_external_identities_subject"
        ),
        schema="users",
    )
    op.create_index(
        "ix_external_identities_user_id",
        "external_identities",
        ["user_id"],
        schema="users",
    )

    op.create_table(
        "organizations",
        sa.Column("organization_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_organizations_name"),
        schema="users",
    )

    op.create_table(
        "departments",
        sa.Column("department_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["users.organizations.organization_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "department_id", "organization_id", name="uq_departments_id_organization"
        ),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_departments_name"),
        schema="users",
    )

    op.create_table(
        "memberships",
        sa.Column("membership_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", UUID(as_uuid=True), nullable=False),
        sa.Column("department_id", UUID(as_uuid=True)),
        sa.Column(
            "active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.UniqueConstraint(
            "user_id",
            "organization_id",
            "department_id",
            name="uq_memberships_user_organization_department",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.accounts.user_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["users.organizations.organization_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["department_id", "organization_id"],
            ["users.departments.department_id", "users.departments.organization_id"],
            name="fk_memberships_department_tenant",
            ondelete="RESTRICT",
        ),
        schema="users",
    )
    op.create_index(
        "uq_memberships_user_organization_no_department",
        "memberships",
        ["user_id", "organization_id"],
        unique=True,
        schema="users",
        postgresql_where=sa.text("department_id IS NULL"),
    )

    op.create_table(
        "role_assignments",
        sa.Column("assignment_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("scope_kind", sa.String(16), nullable=False),
        sa.Column("organization_id", UUID(as_uuid=True)),
        sa.Column("department_id", UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("external_group_id", sa.String(512)),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.accounts.user_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["users.organizations.organization_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["department_id", "organization_id"],
            ["users.departments.department_id", "users.departments.organization_id"],
            name="fk_role_assignments_department_tenant",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "role IN ('designer', 'department_head', 'platform_admin')",
            name="ck_role_assignments_role",
        ),
        sa.CheckConstraint(
            "source IN ('local', 'ad_group')", name="ck_role_assignments_source"
        ),
        sa.CheckConstraint(
            "scope_kind IN ('platform', 'organization', 'department', 'own')",
            name="ck_role_assignments_scope_kind",
        ),
        sa.CheckConstraint(
            "(scope_kind IN ('platform', 'own') AND organization_id IS NULL AND department_id IS NULL) OR "
            "(scope_kind = 'organization' AND organization_id IS NOT NULL AND department_id IS NULL) OR "
            "(scope_kind = 'department' AND organization_id IS NOT NULL AND department_id IS NOT NULL)",
            name="ck_role_assignments_scope",
        ),
        sa.CheckConstraint(
            "(source = 'local' AND external_group_id IS NULL) OR "
            "(source = 'ad_group' AND external_group_id IS NOT NULL AND length(btrim(external_group_id)) > 0)",
            name="ck_role_assignments_group",
        ),
        sa.CheckConstraint(
            "role <> 'platform_admin' OR (scope_kind = 'platform' AND source = 'local')",
            name="ck_role_assignments_admin_scope",
        ),
        sa.CheckConstraint(
            "role <> 'department_head' OR scope_kind = 'department'",
            name="ck_role_assignments_head_scope",
        ),
        sa.CheckConstraint(
            "role <> 'designer' OR scope_kind = 'own'",
            name="ck_role_assignments_designer_scope",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > created_at",
            name="ck_role_assignments_expiry",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_role_assignments_revoke_time",
        ),
        schema="users",
    )
    op.create_index(
        "ix_role_assignments_user_id", "role_assignments", ["user_id"], schema="users"
    )

    op.create_table(
        "admin_bootstrap",
        sa.Column("singleton_id", sa.Integer(), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("assignment_id", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.accounts.user_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["assignment_id"],
            ["users.role_assignments.assignment_id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("singleton_id = 1", name="ck_admin_bootstrap_singleton"),
        schema="users",
    )

    op.create_table(
        "role_assignment_events",
        sa.Column("event_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("assignment_id", UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", UUID(as_uuid=True)),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("authorization_version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["assignment_id"],
            ["users.role_assignments.assignment_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.accounts.user_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "assignment_id", "action", name="uq_role_assignment_events_action"
        ),
        sa.CheckConstraint(
            "action IN ('assign', 'revoke', 'bootstrap')",
            name="ck_role_assignment_events_action",
        ),
        sa.CheckConstraint(
            "(action = 'bootstrap' AND actor_user_id IS NULL) OR "
            "(action IN ('assign', 'revoke') AND actor_user_id IS NOT NULL)",
            name="ck_role_assignment_events_actor",
        ),
        sa.CheckConstraint(
            "authorization_version >= 1", name="ck_role_assignment_events_version"
        ),
        schema="users",
    )
    op.create_index(
        "ix_role_assignment_events_actor",
        "role_assignment_events",
        ["actor_user_id"],
        schema="users",
    )


def downgrade() -> None:
    """Удаляет только собственные таблицы при явно запрошенном откате."""
    op.drop_index(
        "ix_role_assignment_events_actor",
        table_name="role_assignment_events",
        schema="users",
    )
    op.drop_table("role_assignment_events", schema="users")
    op.drop_table("admin_bootstrap", schema="users")
    op.drop_index(
        "ix_role_assignments_user_id", table_name="role_assignments", schema="users"
    )
    op.drop_table("role_assignments", schema="users")
    op.drop_index(
        "uq_memberships_user_organization_no_department",
        table_name="memberships",
        schema="users",
    )
    op.drop_table("memberships", schema="users")
    op.drop_table("departments", schema="users")
    op.drop_table("organizations", schema="users")
    op.drop_index(
        "ix_external_identities_user_id",
        table_name="external_identities",
        schema="users",
    )
    op.drop_table("external_identities", schema="users")
    op.drop_table("accounts", schema="users")
